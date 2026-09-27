"""Measure every cut in spx_jev/cuts.py from the stored SPX sessions, and write spec/cuts.json.

    python3 spec/measure_cuts.py                          # the station's state, read only
    python3 spec/measure_cuts.py --state-dir DIR --out spec/cuts.json

The rule, one for every cut: an SPX cut sits at the same percentile of the same measurement on
SPX history as SNDK JEV's cut sits on SNDK history. SNDK's cuts were chosen or measured for what
they mean ("a real 30-minute move", "flat at 30 minutes", "a heavy strike within reach"); the
share of SNDK observations under the cut is that meaning in numbers, and the SPX value at that
share carries it across without borrowing SNDK's scale. Where SNDK's number was a base rate
(the sums' "about one time in five"), the SPX base rate is counted directly with the SPX band.

What is measured, on every session with at least MIN_BARS minute bars and MIN_ROWS diary rows:
    reads     every READ_STEP_MIN minutes from 09:35 to the last whole horizon before the close,
              on the newest diary row no more than ROW_MAX_AGE_MIN old (its sigma is the ruler)
    rows      every diary row, for the book's fields (walls, strike shares)
    tape      every 5 minutes from 09:35 to 10:30, as the opening lane reads, sigma pinned to the
              day's first row

The declared cuts (the bottom of cuts.py: splits and ratios whose meaning is their own words) keep
their declared values; they are measured to show where each one falls. For every declared constant
the record gives the share of SPX observations under it, and of SNDK observations of the same
measurement, as a percentile beside the measured cuts' ones. The values are read from cuts.py itself,
so the record measures the numbers the labels use. A declared constant nothing on disk can measure
is listed under ``not_measured`` with its reason.
Read only: nothing under the state directory is written. The output is the only file touched.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from bisect import bisect_right
from datetime import date, datetime, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spx_jev import cuts as CUTS  # noqa: E402  (the package beside spec/, for the declared values)

DEFAULT_STATE_DIR = Path.home() / ".claude" / "plugins" / "mirai-station" / "state"
OUT = Path(__file__).resolve().parent / "cuts.json"
MIN_BARS = 300
MIN_ROWS = 100
READ_STEP_MIN = 5
ROW_MAX_AGE_MIN = 10
ONE = timedelta(minutes=1)
RULE = ("each SPX cut sits at the same percentile of the same measurement on SPX history as SNDK JEV's cut "
        "sits on SNDK history; base rates are counted on SPX with the SPX band")

# SNDK JEV's cuts as of 2026-09-26 (skills/sndk-jev: state_builder.py, questions/sndk_hour.json,
# questions/sndk_lane_hour.json). Each is carried to SPX by its percentile, see the module note.
SNDK_CUTS = {
    "move_rule_sigma": ("move30", 0.15),
    "wall_near_sigma": ("wall_distance", 0.5),
    "shape_cut_sigma": ("open_leg", 0.30),
    "outlier_day_sigma": ("prior_close_gap", 1.0),
    "iv_flat_band_pts": ("iv_change30", 2.0),
    "realized_quiet_ratio": ("realized_ratio", 0.7),
    "realized_wild_ratio": ("realized_ratio", 1.3),
    "wall_thin_share": ("wall_share", 0.05),
    "wall_thick_share": ("wall_share", 0.15),
    "grip_spread_share": ("top_strike_share", 0.10),
    "grip_concentrated_share": ("top_strike_share", 0.20),
    "busiest_strike_share": ("busiest_strike_share", 0.20),
    "next_30_flat_band_sigma": ("end30", 0.12),
    "next_30_large_band_sigma": ("far30", 0.30),
    "next_60_flat_band_sigma": ("end60", 0.17),
    "ruler_hold_sigma": ("unit_at_0945", 0.4),
    "ruler_floor_sigma": ("unit_opening", 0.08),
    "tape_flat_units": ("tape_move10", 0.35),
    "tape_big_units": ("tape_move10", 0.7),
}
DECIMALS = {"share": 3, "ratio": 2, "pts": 1}

# The declared cuts, each with the measurements it splits (the label that reads it, in the same terms).
DECLARED_CUTS = {
    "even_split_low": ("expected_move_down_share", "gamma_above_share", "delta_below_share", "call_volume_share"),
    "even_split_high": ("expected_move_down_share", "gamma_above_share", "delta_below_share", "call_volume_share"),
    "minute_width_cut": ("minute_width",),
    "turnover_low": ("turnover",),
    "turnover_high": ("turnover",),
    "path_choppy": ("path_efficiency",),
    "path_orderly": ("path_efficiency",),
    "pace_smaller": ("pace",),
    "pace_bigger": ("pace",),
    "pause_brief_min": ("pause_minutes",),
    "pause_long_min": ("pause_minutes",),
    "pullback_share": ("pullback",),
    "rsi_oversold": ("rsi_1min", "rsi_5min"),
    "rsi_overbought": ("rsi_1min", "rsi_5min"),
    "vix_curve_flat": ("vix_ratio",),
}
NOT_MEASURED = {
    "zero_dte_last_hour_min": "a clock fact, the 0DTE book's last hour before its settle, not a split of any measurement; "
                              "the evidence for treating that hour apart is iv_change30_median_pts",
    "wall_touch_siege_percentile": "a percentile by construction: the siege box ranks each touch's SPY volume against its own "
                            "baseline for the clock window, so the cut sits at the 70th percentile of normal by definition",
    "wall_touch_quiet_percentile": "a percentile by construction, as wall_touch_siege_percentile: the 30th percentile of normal by definition",
    "stress_vix_rise_pts": "the question set's stress rule in its own words (VIX 2 or more points over its open), a fixed level by "
                           "design: a rank goes blind after about 20 stressed sessions, and the 48 sessions hold too few stress days to place it",
    "stress_curve": "the question set's stress rule in its own words (VIX at 0.98 of three-month VIX, all but inverted), a fixed level "
                    "beside vix_curve_flat rather than a split of any measurement",
    "opening_burst_share": "a percentile by construction: before 10:40 a five-minute move is ranked against the same five minutes on "
                           "the prior sessions, so the line sits at the 95th percentile of normal by definition",
    "follow_on_min": "a clock fact: how long after a release its follow-on (the Fed's press conference, 30 minutes after the statement) "
                     "is still read with it, not a split of any measurement",
    "open_10_flat_band_sigma": "the premarket lane's 10-minute sum band from the settled open, the live sum's 30-minute band scaled "
                               "by the square root of 10 over 30 until the settled opens are measured",
}
HOW_DECLARED = {
    "expected_move_down_share": "every row: the day's expected move's down share (adaptive_em.down_share), iv.move_sides",
    "gamma_above_share": "every row: today's 0DTE gamma above spot over above plus below, gex.weight_side",
    "delta_below_share": "every row: one minus the 0-to-7-day books' delta share above spot, gex.delta_weight_side",
    "call_volume_share": "every row: calls over all contracts in the per-strike volume near price, options.call_put_split",
    "turnover": "every row: contracts traded over standing open interest across the per-strike rows, options.turnover",
    "minute_width": "every read with 15 finished bars: the newest bar's range over the median bar range so far today, price.minute_width",
    "pace": "every read 20 minutes in: the last 10 minutes' absolute move over the 10 before (reads where the earlier 10 moved), momentum.pace",
    "path_efficiency": "every read whose 30-minute move passed the move rule: net close-to-close move over the distance the closes travelled, momentum.path_efficiency",
    "pause_minutes": "the same reads: the longest run of minutes without a new extreme in the move's direction, momentum.pauses",
    "pullback": "the same reads: the deepest give-back from the running extreme as a share of the move's full extent, momentum.pauses",
    "rsi_1min": "every read with 15 finished bars: Wilder's 14-period RSI of the 1-minute closes, momentum.rsi_1min",
    "rsi_5min": "every read with 75 finished bars: the same RSI of every fifth 1-minute close, momentum.rsi_5min",
    "vix_ratio": "every row: the VIX over the three-month VIX (vix_ts), iv.term_structure",
}


# ----------------------------------------------------------------------------- reading

def _jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _bars(raw: list[dict]) -> list[dict]:
    bars = [b for b in raw if isinstance(b.get("ts"), str) and all(_num(b.get(k)) for k in ("open", "high", "low", "close"))]
    for b in bars:
        b["t"] = datetime.fromisoformat(b["ts"])
    return sorted(bars, key=lambda b: b["t"])


def _rows(raw: list[dict], ticker: str) -> list[dict]:
    rows = [r for r in raw if r.get("ticker", ticker) == ticker and isinstance(r.get("ts"), str)
            and _num(r.get("spot")) and _num(r.get("sigma")) and r["sigma"] > 0]
    for r in rows:
        r["t"] = datetime.fromisoformat(r["ts"])
    return sorted(rows, key=lambda r: r["t"])


def sessions(state_dir: Path, name: str) -> list[tuple[str, list[dict], list[dict]]]:
    """``[(day, bars, rows)]`` for every full session of ``name`` ("SPX" or "SNDK") on disk."""
    if name == "SPX":
        rows_dir, bar_file = state_dir / "reversion", lambda d: state_dir / "reversion" / "bars" / f"{d}-SPX.json"
        read_bars = lambda p: json.loads(p.read_text(encoding="utf-8"))
    else:
        rows_dir, bar_file = state_dir / "sndk_reversion", lambda d: state_dir / "sndk_bars" / f"{d}.jsonl"
        read_bars = _jsonl
    out = []
    for p in sorted(rows_dir.glob("20??-??-??.jsonl")):
        day, bp = p.stem, bar_file(p.stem)
        if not bp.exists():
            continue
        bars, rows = _bars(read_bars(bp)), _rows(_jsonl(p), name)
        if len(bars) >= MIN_BARS and len(rows) >= MIN_ROWS:
            out.append((day, bars, rows))
    return out


# ----------------------------------------------------------------------------- measuring

def _close_at(bars: list[dict], ends: list[datetime], t: datetime) -> float | None:
    k = bisect_right(ends, t)
    return float(bars[k - 1]["close"]) if k else None


def _window(bars: list[dict], ends: list[datetime], start: datetime, end: datetime) -> list[dict]:
    return bars[bisect_right(ends, start):bisect_right(ends, end)]


def _row_at(rows: list[dict], stamps: list[datetime], t: datetime) -> dict | None:
    k = bisect_right(stamps, t)
    return rows[k - 1] if k and t - stamps[k - 1] <= timedelta(minutes=ROW_MAX_AGE_MIN) else None


def _slice_range(bars, ends, end: datetime) -> float | None:
    win = _window(bars, ends, end - timedelta(minutes=5), end)
    return max(b["high"] for b in win) - min(b["low"] for b in win) if win else None


def _unit(bars, ends, t: datetime) -> float | None:
    ranges = [r for k in range(3) if (r := _slice_range(bars, ends, t - timedelta(minutes=5 * k))) is not None]
    return statistics.median(ranges) if ranges else None


def wilder_rsi(closes: list[float], period: int = 14) -> float | None:
    """labels.measures.wilder_rsi, the same arithmetic."""
    if len(closes) < period + 1:
        return None
    gains = [max(b - a, 0.0) for a, b in zip(closes[:-1], closes[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(closes[:-1], closes[1:])]
    avg_g, avg_l = sum(gains[:period]) / period, sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g, avg_l = (avg_g * (period - 1) + g) / period, (avg_l * (period - 1) + l) / period
    return 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def _move_shape(win: list[dict], start: float, up: bool) -> tuple[float, int, float | None]:
    """A 30-minute move's shape as the momentum labels read it: the longest stall without a new
    extreme, the deepest give-back as a share of the move's extent, and the path efficiency."""
    ext, last_ext, stall, deepest = start, -1, 0, 0.0
    for i, b in enumerate(win):
        deepest = max(deepest, (ext - b["low"]) if up else (b["high"] - ext))
        if up and b["high"] > ext:
            ext, last_ext = b["high"], i
        elif not up and b["low"] < ext:
            ext, last_ext = b["low"], i
        stall = max(stall, i - last_ext if last_ext >= 0 else i + 1)
    extent = abs(ext - start)
    closes = [b["close"] for b in win]
    travel = sum(abs(y - x) for x, y in zip(closes[:-1], closes[1:]))
    eff = abs(closes[-1] - closes[0]) / travel if travel > 0 else None
    return (deepest / extent if extent > 0 else 0.0), stall, eff


def _row_shares(r: dict, obs: dict[str, list[float]]) -> None:
    """The book's splits and ratios on one diary row, for the declared cuts."""
    gv, dv = r.get("gex_views") or {}, r.get("dex_views") or {}
    ds = (r.get("adaptive_em") or {}).get("down_share")
    if _num(ds):
        obs["expected_move_down_share"].append(float(ds))
    above, below = gv.get("gamma_above_spot"), gv.get("gamma_below_spot")
    if _num(above) and _num(below) and above + below > 0:
        obs["gamma_above_share"].append(float(above) / float(above + below))
    if _num(dv.get("dex_above_spot")):
        obs["delta_below_share"].append(1.0 - float(dv["dex_above_spot"]))
    vols = [(float(v[1]), float(v[2])) for v in gv.get("vol_side_by_strike") or []
            if isinstance(v, (list, tuple)) and len(v) >= 3 and _num(v[1]) and _num(v[2])]
    ois = [abs(float(v[1])) + abs(float(v[2])) for v in gv.get("oi_side_by_strike") or []
           if isinstance(v, (list, tuple)) and len(v) >= 3 and _num(v[1]) and _num(v[2])]
    traded = sum(c + p for c, p in vols)
    if traded > 0:
        obs["call_volume_share"].append(sum(c for c, _ in vols) / traded)
        if sum(ois) > 0:
            obs["turnover"].append(traded / sum(ois))
    if _num(r.get("vix_ts")) and r["vix_ts"] > 0:
        obs["vix_ratio"].append(float(r["vix_ts"]))


def measure(day_bars_rows: list[tuple[str, list[dict], list[dict]]], hold_sigma: float | None = None,
            move_rule: float | None = None) -> dict[str, list[float]]:
    """Every measurement over every session. ``hold_sigma`` is the held tape unit used before 09:45
    for the tape moves; without it the tape moves are not measured (the SNDK side passes SNDK's,
    the SPX side passes the SPX hold measured first). ``move_rule`` is the 30-minute move past which
    the momentum labels judge a move's shape; without it the shape is not measured."""
    obs: dict[str, list[float]] = {k: [] for k, _ in set(SNDK_CUTS.values())}
    obs.update({"signed_end30": [], "signed_end60": [], "tape_signed_move10": [], "iv_change30_last_hour": []})
    obs.update({m: [] for ms in DECLARED_CUTS.values() for m in ms})
    for day, bars, rows in day_bars_rows:
        ends = [b["t"] + ONE for b in bars]
        stamps = [r["t"] for r in rows]
        d = date.fromisoformat(day)
        open_t = datetime.combine(d, datetime.min.time(), tzinfo=bars[0]["t"].tzinfo).replace(hour=9, minute=30)
        close_t = open_t.replace(hour=16, minute=0)
        open0 = float(bars[0]["open"])
        t = open_t + timedelta(minutes=5)
        while t < close_t:
            row = _row_at(rows, stamps, t)
            c0 = _close_at(bars, ends, t)
            if row is not None and c0 is not None:
                sigma = float(row["sigma"])
                done = bars[:bisect_right(ends, t)]
                if len(done) >= 15:
                    typical = statistics.median(b["high"] - b["low"] for b in done)
                    if typical > 0:
                        obs["minute_width"].append((done[-1]["high"] - done[-1]["low"]) / typical)
                    closes = [b["close"] for b in done]
                    for key, series in (("rsi_1min", closes), ("rsi_5min", closes[4::5])):
                        if (rsi := wilder_rsi(series)) is not None:
                            obs[key].append(rsi)
                if t >= open_t + timedelta(minutes=20):
                    c10, c20 = _close_at(bars, ends, t - timedelta(minutes=10)), _close_at(bars, ends, t - timedelta(minutes=20))
                    if c10 is not None and c20 is not None and c10 != c20:
                        obs["pace"].append(abs(c0 - c10) / abs(c10 - c20))
                if t >= open_t + timedelta(minutes=30):
                    back = _close_at(bars, ends, t - timedelta(minutes=30))
                    if back is not None:
                        obs["move30"].append(abs(c0 - back) / sigma)
                        shape_win = _window(bars, ends, t - timedelta(minutes=30), t)
                        if move_rule is not None and abs(c0 - back) / sigma >= move_rule and len(shape_win) >= 20:
                            pullback, stall, eff = _move_shape(shape_win, back, c0 > back)
                            obs["pullback"].append(pullback)
                            obs["pause_minutes"].append(float(stall))
                            if eff is not None:
                                obs["path_efficiency"].append(eff)
                    obs["open_leg"].append(abs(c0 - open0) / sigma)
                    win = _window(bars, ends, t - timedelta(minutes=30), t)
                    if len(win) >= 25 and back is not None:
                        closes = [back] + [b["close"] for b in win]
                        span = (ends[bisect_right(ends, t) - 1] - (t - timedelta(minutes=30))).total_seconds() / 60.0
                        ss = sum((b - a) ** 2 for a, b in zip(closes[:-1], closes[1:])) * (30 / span)
                        obs["realized_ratio"].append(math.sqrt(ss) / sigma / math.sqrt(30 / 390))
                    earlier = _row_at(rows, stamps, t - timedelta(minutes=30))
                    if earlier is not None and _num(row.get("atm_iv")) and _num(earlier.get("atm_iv")):
                        change = abs(float(row["atm_iv"]) - float(earlier["atm_iv"])) * 100.0
                        obs["iv_change30_last_hour" if close_t - t <= timedelta(minutes=60) else "iv_change30"].append(change)
                for h in (30, 60):
                    mark = t + timedelta(minutes=h)
                    if mark > close_t:
                        continue
                    c1 = _close_at(bars, ends, mark)
                    win = _window(bars, ends, t, mark)
                    if c1 is None or not win or ends[bisect_right(ends, mark) - 1] < mark - timedelta(minutes=2):
                        continue
                    obs[f"end{h}"].append(abs(c1 - c0) / sigma)
                    obs[f"signed_end{h}"].append((c1 - c0) / sigma)
                    if h == 30:
                        obs["far30"].append(max(max(b["high"] for b in win) - c0, c0 - min(b["low"] for b in win)) / sigma)
            t += timedelta(minutes=READ_STEP_MIN)
        for r in rows:
            _row_shares(r, obs)
            spot, sigma = float(r["spot"]), float(r["sigma"])
            walls = [(abs(float(r[k]) - spot) / sigma, k) for k in ("call_wall", "put_wall") if _num(r.get(k))]
            gv = r.get("gex_views") or {}
            if walls:
                dist, key = min(walls)
                obs["wall_distance"].append(dist)
                share = gv.get("call_wall_gamma_share" if key == "call_wall" else "put_wall_gamma_share")
                if _num(share):
                    obs["wall_share"].append(float(share))
            if _num(r.get("prior_close")) and r["prior_close"] > 0:
                obs["prior_close_gap"].append(abs(spot - float(r["prior_close"])) / sigma)
            if _num(gv.get("pin_top_share")):
                obs["top_strike_share"].append(float(gv["pin_top_share"]))
            vols = [float(v[1]) for v in gv.get("vol_gross_by_strike") or [] if isinstance(v, (list, tuple)) and len(v) >= 2 and _num(v[1])]
            if sum(vols) > 0:
                obs["busiest_strike_share"].append(max(vols) / sum(vols))
        first_sigma = float(rows[0]["sigma"])
        for k in range(12):
            t = open_t + timedelta(minutes=5 + 5 * k)
            unit = _unit(bars, ends, t) if t >= open_t + timedelta(minutes=15) else None
            if unit is not None:
                obs["unit_opening"].append(unit / first_sigma)
                if k == 2:
                    obs["unit_at_0945"].append(unit / first_sigma)
            if hold_sigma is None:
                continue
            u = unit if unit is not None else hold_sigma * first_sigma
            c0, c1 = _close_at(bars, ends, t), _close_at(bars, ends, t + timedelta(minutes=10))
            if c0 is not None and c1 is not None and u > 0:
                obs["tape_move10"].append(abs(c1 - c0) / u)
                obs["tape_signed_move10"].append((c1 - c0) / u)
    return obs


def share_below(values: list[float], cut: float) -> float:
    return sum(1 for v in values if v < cut) / len(values)


def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo)


def _rounding(name: str) -> int:
    for suffix, nd in DECIMALS.items():
        if name.endswith(suffix):
            return nd
    return 2


def carry(sndk: dict[str, list[float]], spx: dict[str, list[float]], names: list[str]) -> dict[str, dict]:
    out = {}
    for name in names:
        measure_name, sndk_cut = SNDK_CUTS[name]
        q = share_below(sndk[measure_name], sndk_cut)
        out[name] = {"value": round(quantile(spx[measure_name], q), _rounding(name)), "measure": measure_name,
                     "sndk_cut": sndk_cut, "percentile": round(100 * q, 1),
                     "n_sndk": len(sndk[measure_name]), "n_spx": len(spx[measure_name])}
    return out


def declared(sndk: dict[str, list[float]], spx: dict[str, list[float]]) -> dict[str, dict]:
    """Each declared constant at its declared value, and where it falls on each measurement it splits:
    the percentile (the share of observations under it) on SPX and on SNDK."""
    out = {}
    for name, measures in DECLARED_CUTS.items():
        value = getattr(CUTS, name.upper())
        at = {}
        for m in measures:
            at[m] = {"spx_percentile": round(100 * share_below(spx[m], value), 1) if spx[m] else None, "n_spx": len(spx[m]),
                     "sndk_percentile": round(100 * share_below(sndk[m], value), 1) if sndk[m] else None, "n_sndk": len(sndk[m]),
                     "how": HOW_DECLARED[m]}
        out[name] = {"value": value, "measures": at}
    return out


def pct_of(values: list[float], test) -> int:
    return round(100 * sum(1 for v in values if test(v)) / len(values))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the SPX cuts from stored sessions (read only).")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    state_dir = Path(args.state_dir)
    spx_sessions, sndk_sessions = sessions(state_dir, "SPX"), sessions(state_dir, "SNDK")
    sndk = measure(sndk_sessions, hold_sigma=SNDK_CUTS["ruler_hold_sigma"][1], move_rule=SNDK_CUTS["move_rule_sigma"][1])
    first = carry(sndk, measure(spx_sessions), ["ruler_hold_sigma", "move_rule_sigma"])
    spx = measure(spx_sessions, hold_sigma=first["ruler_hold_sigma"]["value"], move_rule=first["move_rule_sigma"]["value"])
    cuts = carry(sndk, spx, list(SNDK_CUTS))
    base_rates = {}
    for h in (30, 60):
        band = cuts[f"next_{h}_flat_band_sigma"]["value"]
        v = spx[f"signed_end{h}"]
        base_rates[f"next_{h}"] = {"up_pct": pct_of(v, lambda x: x > band), "down_pct": pct_of(v, lambda x: x < -band),
                                   "flat_pct": pct_of(v, lambda x: abs(x) <= band), "n": len(v)}
    flat_u, big_u = cuts["tape_flat_units"]["value"], cuts["tape_big_units"]["value"]
    v = spx["tape_signed_move10"]
    base_rates["next_10"] = {"down_big_pct": pct_of(v, lambda x: x < -big_u), "down_small_pct": pct_of(v, lambda x: -big_u <= x < -flat_u),
                             "flat_pct": pct_of(v, lambda x: abs(x) <= flat_u), "up_small_pct": pct_of(v, lambda x: flat_u < x <= big_u),
                             "up_big_pct": pct_of(v, lambda x: x > big_u), "n": len(v)}
    last_hour = spx["iv_change30_last_hour"]
    doc = {
        "name": "spx-jev cuts, measured",
        "measured_on": datetime.now().astimezone().date().isoformat(),
        "rule": RULE,
        "spx_sessions": [d for d, _, _ in spx_sessions],
        "sndk_sessions": [d for d, _, _ in sndk_sessions],
        "cuts": cuts,
        "base_rates": base_rates,
        "iv_change30_median_pts": {"before_the_last_hour": round(statistics.median(spx["iv_change30"]), 2),
                                   "in_the_last_hour": round(statistics.median(last_hour), 2) if last_hour else None},
        "declared_rule": ("declared cuts keep their declared values, whose meaning is their own words; each is measured "
                          "for where it falls: the percentile is the share of observations under it"),
        "declared": declared(sndk, spx),
        "not_measured": NOT_MEASURED,
    }
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"{len(spx_sessions)} SPX sessions, {len(sndk_sessions)} SNDK sessions -> {args.out}")
    for name, c in cuts.items():
        print(f"  {name:28s} {c['value']:>8}   (SNDK {c['sndk_cut']} at the {c['percentile']}th percentile; n {c['n_spx']})")
    print(f"  base rates {base_rates}")
    print(f"  IV change over 30 min, median points: {doc['iv_change30_median_pts']}")
    for name, d in doc["declared"].items():
        for m, at in d["measures"].items():
            print(f"  declared {name:22s} {d['value']:>6}  {m:26s} SPX {at['spx_percentile']}th (n {at['n_spx']}), "
                  f"SNDK {at['sndk_percentile']}th (n {at['n_sndk']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
