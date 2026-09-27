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

# The sums (questions/spx_hour.json): flat within the band at the mark.
NEXT_30_FLAT_BAND_SIGMA = 0.07
# Measured for the "reach 0.16 sigma within 30 minutes" tradeability outcome the set asks code to add; nothing
# grades a large band yet, so nothing reads it (a clean-up candidate, as OUTLIER_DAY_SIGMA).
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
# A stress day, the only kind vol.stress_path is written on (the set's words): VIX this many points over its
# open, over its prior sessions' high, or VIX at this share of three-month VIX, all but inverted.
STRESS_VIX_RISE_PTS = 2.0
STRESS_CURVE = 0.98
# Before 10:40 no window has an hour after the settled open behind it: a burst is then a move in the top
# 5% for its five minutes over the prior sessions (the set's opening bursts).
OPENING_BURST_SHARE = 0.95
# A follow-on (the Fed's press conference) is read with the release it follows when due within this many minutes of it.
FOLLOW_ON_MIN = 45

# ---- the question set's constants (spec/question_set.json "constants"), in the set's order, each with its
# note from there. Most are declared in the set's own words and not yet placed on SPX history; the few it
# shares with the cuts above (move_rule_sigma, path_choppy, vix_curve_flat, the even split, the siege
# percentile) are defined once, above. A test holds every one of them to the set.
MOVE_STRONG_SIGMA = 0.2  # about the 90th percentile of |30-minute move|
DAY_SIDE_SIGMA = 0.2  # day move from yesterday's close counted as a side
GAP_RULE_SIGMA = 0.15  # real gap; about half the opening straddle (sigma_anchor ~3.4x em_open)
GAP_LARGE_SIGMA = 0.4  # large gap line; old 0.80 fired on 2-3 of 59 days
GAP_HALF_SHARE = 0.5  # half-way share for gap kept, shock giveback, reaction giveback
GAP_TOUCH_SIGMA = 0.02  # touch tolerance for yesterday's close
GAP_RANK_MIN_SESSIONS = 10  # fewest prior sessions with a trusted ruler to rank the gap's size against
GIVEBACK_THIRD = 0.33  # stall line for an opening drive
RANGE_TOP_SHARE = 0.75  # top quarter of today's range
RANGE_BOTTOM_SHARE = 0.25  # bottom quarter of today's range
OPEN_CONTESTED_CROSSES = 6  # crosses of the settled open for 'contested'
ONE_CROSS = 1  # one crossing
HEAVY_SHOCK_SIGMA = 0.15  # one name's overnight contribution to SPX, in SPX sigma
REST_GAP_SIGMA = 0.1  # rest-of-index gap rule
OPEN_VOL_Z = 0.75  # net-volume lean in same-minute standard deviations
OPEN_CLUSTER_MIN = 3  # bursts that make a cluster
TICK_BURST_PCT = 0.95  # same-minute percentile of $TICK bar highs/lows that counts as a burst
OPEN_TICK_LEAN = 100  # mean $TICK since the open counted as a lean
BIG_PRINT_LOTS = 100  # large single 0DTE trade
BIG_LEAN_SHARE = 0.65  # premium share for a lean
BIG_MIN_PRINTS = 5  # minimum large trades
CALL_PUT_SHIFT_SHARE = 0.1  # call-share swing of new 0DTE volume vs the day
THIN_VOLUME_PCT = 0.2  # same-clock percentile under which new volume is too thin
MIN_RANK_SESSIONS = 5  # fewest prior sessions for a same-clock rank
SKEW_STEEP_RANK = 0.8  # 8 of the last 10 sessions at this minute
SKEW_FLAT_RANK = 0.2  # 2 of the last 10
TOP_FIFTH = 0.8  # top-fifth rank
BOTTOM_FIFTH = 0.2  # bottom-fifth rank
HALF_RANK = 0.5  # median rank
THIRD_HI = 0.67  # top-third rank
THIRD_LO = 0.33  # bottom-third rank
WINDOW_10_MIN = 10  # window in minutes
WINDOW_30_MIN = 30  # window in minutes
WINDOW_60_MIN = 60  # window in minutes
MOVE_BURST_SHARE = 0.8  # one 5-minute chunk's share of the 30-minute move for a burst
ONE_WAY_HOUR_SIGMA = 0.2  # hour move size for the one-way question
ONE_WAY_CROSSES = 3  # crosses of the hourly mean under which an hour is one-way
VR_TREND = 1.1  # variance ratio for 'moves building'
VR_PIN = 0.9  # variance ratio for 'moves cancelling'
IB_BREAK_SIGMA = 0.03  # first-hour break buffer
IB_EXTEND_SIGMA = 0.25  # first-hour extension line
NOISE_EDGE_SIGMA = 0.05  # noise-band edge buffer
NOISE_LOOKBACK = 14  # sessions in the noise band average
STRADDLE_CHEAP = 0.85  # straddle share cheap line
STRADDLE_RICH = 1.15  # straddle share rich line
VIX_STILL_PCT = 0.006  # 30-minute VIX change / level: still (0.10 points at VIX 15.8)
VIX_MOVE_PCT = 0.013  # moving (0.20 points at 15.8)
VIX_JUMP_PCT = 0.022  # jumping (0.35 points at 15.8)
FLAT_REACH_NARROW_30 = 0.53  # flat band / 30-min reach, narrow tercile cut
FLAT_REACH_WIDE_30 = 0.7  # wide tercile cut
FLAT_REACH_NARROW_60 = 0.59  # 60-min narrow cut
FLAT_REACH_WIDE_60 = 0.77  # 60-min wide cut
RANGE_PACE_SLEEPY = 0.7  # range over one-sigma-for-time, under
ONE_RATIO = 1.0  # one-to-one line
RANGE_PACE_WILD = 1.4  # far-over line
SPY_SPREAD_WIDE = 0.025  # SPY spread in dollars: 3 cents or more
SPY_SPREAD_TIGHT = 0.015  # SPY spread in dollars: 1 cent
VIX_RESID_PCT = 0.013  # VIX residual beyond the SPX-explained move, share of VIX level (about 0.20 points)
PAIR_MOVE_SIGMA = 0.05  # SPX move for the VVIX pairing
FRONT_SHIFT_PTS = 0.15  # 30-minute change in VIX9D minus VIX, points
SKEW_RESID_CUT = 0.02  # put-call tilt change beyond price, share of ATM IV
ATM_RESID_VOLPTS = 0.5  # ATM 0DTE IV residual, vol points
EVENT_DUE_MIN = 60  # event due window
EVENT_DIGEST_MIN = 120  # digest window after a release
SPEAKER_WINDOW_MIN = 60  # Fed speaker window
LOADED_RATIO = 1.5  # straddle-priced 30-min move over tape-delivered move
RULER_HIGH = 1.3  # sigma_anchor over 20-session median: swollen
RULER_LOW = 0.8  # compressed
VIX_CURVE_NEAR_FLAT = 0.95  # near-flat line
TICK_CLUSTER = 3  # TICK extreme readings in 30 minutes
TICK_EXTREME = 1000  # NYSE TICK extreme level
NEAR_LOW_SIGMA = 0.1  # near the session low
STRESS_RETREAT_SHARE = 0.3  # VIX retreat share from its session high for exhaustion
BOUNCE_SIGMA = 0.15  # bounce off the low in normal-day sigma
STRESS_HOLD_SHARE = 0.1  # VIX retreat share under which a bounce is not believed
FLIP_FAR_SIGMA = 0.5  # far from the gamma flip
RV_HOT = 1.3  # 30-min realized over its same-clock median: hot
CUSHION_THIN = 0.5  # share of today's call-heavy balance left after a move
MAGNET_SEAT_SIGMA = 0.1  # seated on the magnet
MAGNET_NEAR_SIGMA = 0.3  # near the magnet
PIN_HUG_HI = 0.7  # share of the window spent near the magnet
BOX_TIGHT_SIGMA = 0.15  # tight wall box
BOX_WIDE_SIGMA = 0.3  # wide wall box
WALL_TOUCH_SIGMA = 0.1  # beyond or back from a wall
DEFENSE_NEAR_SIGMA = 0.25  # contested strike reach
DEFENSE_MIN_EVENTS = 10  # hits for a defense read
VALUE_EDGE_SIGMA = 0.02  # beyond yesterday's value area
ACCEPT_MINUTES = 20  # minutes beyond a level for acceptance
LEVEL_REACH_SIGMA = 0.05  # pressing a level
CROSS_LOOKBACK_MIN = 20  # round-level cross window
ROUND_NEAR_SIGMA = 0.06  # near a round 50/100-point level
ON_BREAK_SIGMA = 0.05  # beyond an overnight edge
ON_NEAR_SIGMA = 0.15  # near an overnight edge
UPVOL_LEAN_HI = 0.6  # NYSE up-volume share lean up
UPVOL_LEAN_LO = 0.4  # lean down
DAY_ONE_SIDED = 0.8  # day up-volume share for one-sided
CHOP_CROSSES = 4  # even-line crossings for rotational
TICK_USUAL_BAND = 0.15  # TICK above-zero share vs same-slot median: usual
TICK_FAR_BAND = 0.3  # far
SMALLCAP_CONFIRM_SIGMA = 0.15  # IWM within this of its own extreme confirms
MEMBER_SPLIT_Z = 0.5  # S&P members vs NYSE net volume split
EQW_SPLIT_SIGMA = 0.1  # equal-weight beta residual split
SECTOR_ONE_WAY = 8  # sectors moving with the index
SECTOR_COUNT = 11  # sector funds
DISPERSION_WIDE_SIGMA = 0.5  # sector dispersion for rotation
MEGA_TOGETHER = 6  # megacaps moving together
MEGA_COUNT = 8  # megacap basket size
MEGA_ONE_NAME_SHARE = 0.33  # one name's share of the index move
PULL_RULE_SIGMA = 0.1  # top-8 vs rest pull rule
SIZE_SPREAD_SIGMA = 0.15  # QQQ minus IWM residual spread
SIZE_RESID_SIGMA = 0.1  # own residual for both ahead/behind
BOND_LINK_TIGHT = 0.25  # 60-minute stock-bond correlation for a tight link
ONE_DAY = 1  # one trading day
OPEX_WEEK_DAYS = 4  # trading days before monthly expiry
AFTER_OPEX_DAYS = 5  # trading days after
REBAL_WINDOW_DAYS = 2  # last trading days of the month for rebalancing
MONTH_TURN_DAYS = 3  # first trading days of the month
SHOCK_LOOKBACK_MIN = 60  # shock lookback
SHOCK_Z = 3.0  # 5-minute move over bipower-implied move
SHOCK_FLOOR_SIGMA = 0.1  # shock floor size
SHOCK_FRESH_MIN = 10  # fresh shock window
EVENT_REACTION_RULE = 0.15  # first reaction rule, normal-day sigma
REACTION_EXTEND_SIGMA = 0.05  # beyond the reaction end
VIX_SHOCK_RESID = 0.15  # VIX points beyond price-explained during a shock
RATES_SHOCK_BP = 3  # ten-year yield basis points in the burst
SHOCK_GROUP_SIGMA = 0.1  # group residual during a burst
ONE_NAME_SHARE = 0.35  # megacap share of a burst
SECTOR_BROAD = 9  # sector funds for a broad shock
SPREAD_COUNT = 2  # other giants for spreading
NAME_SHOCK_MULT = 3.0  # 10-min move over same-clock median for a single-name shock
AFTERNOON_LEG_SIGMA = 0.08  # 14:00-to-now leg rule
SETTLE_SEAT_SIGMA = 0.05  # seated for the settle
PIN_REACH_FAR = 1.5  # remaining straddles for out of reach
CHARM_NEAR_LO = 0.1  # charm wall near low
CHARM_NEAR_HI = 0.5  # charm wall near high
PRESSER_RULE_SIGMA = 0.15  # press-conference move, normal-day sigma
STATEMENT_QUIET_SIGMA = 0.05  # statement move under which it is quiet
OUTSIDE_BUFFER_SIGMA = 0.03  # buffer beyond a level
ON_QUIET = 0.7  # overnight range over the 20-night median: quiet
ON_WIDE = 1.3  # wide
ORIGIN_SHARE = 0.5  # share of the gap made in one window
VIX_GAP_RESID_PTS = 0.4  # opening VIX beyond the gap-implied level, points
RELEASE_SMALL_SIGMA = 0.1  # first release reaction too small
BRIEF_DIR_MIN = 0.2  # brief lean on a -1..+1 scale
BRIEF_CONF_MIN = 0.3  # brief confidence
ROTATION_GAP_SIGMA = 0.1  # sensitive minus defensive sector residual
FLOW_LEAN_RANK = 0.8  # flow lean rank vs clock
OFI_Z = 1.0  # order-flow imbalance z
COLLAR_NEAR_SIGMA = 0.5  # near a quarter-end collar strike
OPEN_DAY_MOVE_SIGMA = 0.1  # day move from the settled open counted as a side; measured here: |spot - settled open| exceeds 0.10 sigma on 75% of 30-minute reads (649 reads, 59 sessions), the same fire rate day_side_sigma (0.20) has on the move from yesterday's close
DEFENSE_REFILL_SHARE = 0.7  # share of hits refilled within seconds for 'defended' (provisional; measure on lob_flow before weights count)
VIX_STILL_PCT_10 = 0.0035  # 10-minute VIX still line, the 30-minute band scaled by sqrt(10/30); refit on the lane
VIX_MOVE_PCT_10 = 0.0075  # 10-minute VIX moving line (scaled); refit on the lane
VIX_JUMP_PCT_10 = 0.013  # 10-minute VIX jump line (scaled); refit on the lane
TICK_FOLLOW_SIGMA = 0.03  # SPX 5-minute move that counts as following a TICK burst (provisional)
STRADDLE_REPRICE_SHARE = 0.05  # straddle richer or cheaper than the clock alone would leave it over 30 minutes (provisional)
TRIN_LOW = 0.85  # NYSE TRIN under which volume runs ahead on rising stocks (provisional)
TRIN_HIGH = 1.15  # NYSE TRIN over which volume runs ahead on falling stocks (provisional)
CLOSE_PUSH_SIGMA = 0.15  # distance to yesterday's close inside which a late push is judged (provisional)
VWAP_TOUCH_SIGMA = 0.02  # touch tolerance for the day's volume-weighted average price

# What a question doc may name in braces. The key is the constant's name in lower case.
QUESTION_CONSTANTS = {name.lower(): value for name, value in dict(globals()).items()
                      if name.isupper() and isinstance(value, (int, float))}
