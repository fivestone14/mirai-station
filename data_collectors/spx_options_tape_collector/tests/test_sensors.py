"""sensors — bucketing, tape signing, refill defense, purge, fade dial."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lob_flow.sensors import (aggregate_sweep, bucket_of, fade_dial,
                              purge_step, read_tape, refill_half_lives)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)

# --- bucketing ---------------------------------------------------------------


def test_bucket_of(cfg):
    assert bucket_of(0.05, cfg) == "d00_10"
    assert bucket_of(-0.05, cfg) == "d00_10"        # abs()
    assert bucket_of(0.30, cfg) == "d25_40"
    assert bucket_of(0.49, cfg) == "d40_50"
    assert bucket_of(0.50, cfg) == "d40_50"         # dead-ATM stays in the map
    assert bucket_of(0.60, cfg) is None             # ITM: outside the watched map
    assert bucket_of(None, cfg) is None


def _atm_quotes(quote_factory, bid_size=40, ask_size=60):
    """>= bucket_min_contracts (4) quotes in d40_50 + 4 in d00_10."""
    qs = [quote_factory(strike=6290 + 5 * i, delta=0.42 + 0.02 * i,
                        bid_size=bid_size, ask_size=ask_size) for i in range(4)]
    qs += [quote_factory(strike=6400 + 5 * i, delta=0.05, bid=0.5, ask=0.7,
                         bid_size=100, ask_size=100) for i in range(4)]
    return qs


def test_aggregate_sweep_medians_and_revisions(cfg, quote_factory):
    agg = aggregate_sweep(_atm_quotes(quote_factory), None, cfg)
    assert agg["buckets"]["d40_50"]["size"] == 100.0          # 40+60 per contract
    assert agg["buckets"]["d40_50"]["bid_size"] == 40.0       # per-side medians
    assert abs(agg["buckets"]["d00_10"]["spread"] - 0.2) < 1e-9
    assert agg["revisions"] == 0                              # no previous sweep
    assert agg["deltas"]["6290.0|call"] == 0.42               # tape weighting map
    # second sweep: one contract changed -> one revision
    quotes2 = _atm_quotes(quote_factory)
    quotes2[0] = quote_factory(strike=6290, delta=0.42, bid_size=41, ask_size=60)
    agg2 = aggregate_sweep(quotes2, agg["fingerprint"], cfg)
    assert agg2["revisions"] == 1
    assert agg2["purge_hint"]["buckets_stepped"] == 0         # nothing stepped


def test_aggregate_sweep_min_contracts_gate(cfg, quote_factory):
    """A bucket with fewer than bucket_min_contracts has NO median — one
    lonely contract must not drive scatter or purge."""
    thin = [quote_factory(strike=6300, delta=0.50, bid_size=40, ask_size=60)]
    agg = aggregate_sweep(thin, None, cfg)
    assert agg["buckets"]["d40_50"]["size"] is None
    assert agg["buckets"]["d40_50"]["n"] == 1


def test_aggregate_sweep_purge_hint_matched_contracts(cfg, quote_factory):
    """The hint counts MATCHED contracts stepping down — immune to a spot
    move reshuffling bucket membership."""
    first = aggregate_sweep(_atm_quotes(quote_factory), None, cfg)
    crashed = _atm_quotes(quote_factory, bid_size=2, ask_size=2)
    crashed = crashed[:4] + [quote_factory(strike=6400 + 5 * i, delta=0.05,
                                           bid=0.5, ask=0.7, bid_size=2,
                                           ask_size=2) for i in range(4)]
    agg2 = aggregate_sweep(crashed, first["fingerprint"], cfg)
    assert agg2["purge_hint"]["buckets_stepped"] >= 2
    assert agg2["purge_hint"]["matched"] == 8


def test_aggregate_sweep_skips_junk(cfg, quote_factory):
    quotes = [quote_factory(delta=None),                       # unbucketable
              quote_factory(bid=None, ask=None, bid_size=None, ask_size=None)]
    agg = aggregate_sweep(quotes, None, cfg)
    assert agg["buckets"].get("d40_50", {}).get("size") is None


# --- tape reader (VWAP-in-spread) ---------------------------------------------


def test_tape_sign_table(cfg, trade_factory):
    """at-ask call buy = bullish; at-bid call = bearish; at-ask put = bearish;
    at-bid put = bullish; mid = indeterminate."""
    assert read_tape([trade_factory(price=10.2)], cfg)["tilt"] > 0.9
    assert read_tape([trade_factory(price=10.0)], cfg)["tilt"] < -0.9
    assert read_tape([trade_factory(price=10.2, right="put")], cfg)["tilt"] < -0.9
    assert read_tape([trade_factory(price=10.0, right="put")], cfg)["tilt"] > 0.9
    mid = read_tape([trade_factory(price=10.1)], cfg)
    assert mid["tilt"] == 0.0 and mid["n_indeterminate"] == 1


def test_tape_vwap_bucket_not_per_trade(cfg, trade_factory):
    """One big at-ask + one tiny at-bid at the same strike: the BUCKET VWAP
    sits near the ask -> net buy (per-trade voting would read 50/50)."""
    trades = [trade_factory(price=10.2, size=90, ts_ms=1),
              trade_factory(price=10.0, size=10, ts_ms=2)]
    assert read_tape(trades, cfg)["tilt"] > 0.5


def test_tape_sequencing_bug_tolerance(cfg, trade_factory):
    """A trade whose attached quote is crossed/garbled must not flip the sign
    of a clean tape (the OPRA sequencing defense: never resort, judge each
    print against its own quote)."""
    clean = [trade_factory(price=10.2, size=50, ts_ms=1)]
    garbled = clean + [trade_factory(price=10.1, size=5, bid=10.2, ask=10.0, ts_ms=0)]
    assert read_tape(garbled, cfg)["tilt"] > 0.5


def test_tape_crossed_and_locked_quotes(cfg, trade_factory):
    locked = read_tape([trade_factory(bid=10.1, ask=10.1, price=10.1)], cfg)
    assert locked["tilt"] == 0.0                              # zero half-spread
    assert read_tape([], cfg)["tilt"] == 0.0


def test_tape_per_strike_split(cfg, trade_factory):
    trades = [trade_factory(strike=6300, price=10.2, size=50, ts_ms=1),
              trade_factory(strike=6310, price=10.0, size=50, ts_ms=2)]
    per = read_tape(trades, cfg)["per_strike"]
    assert per[6300] > 0 and per[6310] < 0


def test_tape_determinate_share_and_dropped(cfg, trade_factory):
    """A tilt built on a sliver of the flow says so: 95% mid prints + one
    at-ask print -> tilt positive but determinate_share tiny."""
    trades = [trade_factory(price=10.1, size=95, ts_ms=1, strike=6300),
              trade_factory(price=10.2, size=5, ts_ms=2, strike=6310)]
    out = read_tape(trades, cfg)
    assert out["tilt"] > 0.5
    assert out["determinate_share"] is not None and out["determinate_share"] < 0.1
    dropped = read_tape([trade_factory(bid=None)], cfg)
    assert dropped["n_dropped"] == 1


# --- refill defense (the huddle, at a FIXED strike) -----------------------------


def _refill_events(trade_factory, recover_after_s, n=4, strike=6300.0,
                   right="call"):
    """Each trade eats half the ask; a later event shows size back."""
    evs = []
    t = 0
    for _ in range(n):
        evs.append(trade_factory(ts_ms=t, strike=strike, right=right,
                                 price=10.2, size=50, ask_size=100))
        evs.append(trade_factory(ts_ms=t + int(recover_after_s * 1000),
                                 strike=strike, right=right, price=10.2,
                                 size=1, ask_size=90))        # recovered >= 75%
        t += 200_000
    return evs


def test_refill_defended_vs_abandoned(cfg, trade_factory):
    fast = refill_half_lives(_refill_events(trade_factory, 20), 6300.0, cfg)
    assert fast["verdict"] == "defended"
    assert fast["refill_half_life_s"] == 20.0
    slow = refill_half_lives(_refill_events(trade_factory, 120), 6300.0, cfg)
    assert slow["verdict"] == "abandoned"


def test_refill_monotone_in_speed(cfg, trade_factory):
    h1 = refill_half_lives(_refill_events(trade_factory, 5), 6300.0, cfg)
    h2 = refill_half_lives(_refill_events(trade_factory, 40), 6300.0, cfg)
    assert h1["refill_half_life_s"] < h2["refill_half_life_s"]


def test_refill_thin_and_unrecovered(cfg, trade_factory):
    thin = refill_half_lives([trade_factory(ask_size=100, size=50)], 6300.0, cfg)
    assert thin["verdict"] == "thin"
    # size bleeds away trade after trade and never rebuilds -> abandoned
    bleeding = [trade_factory(ts_ms=i * 200_000, price=10.2, ask_size=a, size=30)
                for i, a in enumerate((100, 60, 30, 15))]
    gone = refill_half_lives(bleeding, 6300.0, cfg)
    assert gone["verdict"] == "abandoned" and gone["n_unrecovered"] == 4


def test_refill_only_counts_this_strike(cfg, trade_factory):
    evs = _refill_events(trade_factory, 20, strike=6310.0)
    assert refill_half_lives(evs, 6300.0, cfg)["verdict"] == "thin"


def test_refill_ignores_tiny_bites(cfg, trade_factory):
    evs = [trade_factory(ts_ms=0, size=1, ask_size=100)]      # 1% bite
    assert refill_half_lives(evs, 6300.0, cfg)["n_events"] == 0


def test_refill_never_mixes_call_and_put_books(cfg, trade_factory):
    """THE re-review case: calls get eaten and never rebuild; a fat PUT book
    at the same strike must not fake the calls' recovery."""
    calls_bleeding = [trade_factory(ts_ms=i * 200_000, right="call", price=10.2,
                                    ask_size=a, size=30)
                      for i, a in enumerate((100, 60, 30, 15))]
    fat_puts = [trade_factory(ts_ms=i * 200_000 + 1000, right="put",
                              price=10.1, size=1, ask_size=500)
                for i in range(4)]
    out = refill_half_lives(calls_bleeding + fat_puts, 6300.0, cfg)
    assert out["verdict"] == "abandoned"                      # puts didn't vouch
    assert out["n_unrecovered"] >= 3


def test_refill_one_rebuild_closes_one_eat(cfg, trade_factory):
    """Three eats then ONE rebuild: a single rebuild can't vouch for all
    three bites (n_events would treble toward min_events)."""
    evs = [trade_factory(ts_ms=0, price=10.2, size=50, ask_size=100),
           trade_factory(ts_ms=10_000, price=10.2, size=50, ask_size=20),
           trade_factory(ts_ms=20_000, price=10.2, size=50, ask_size=20),
           trade_factory(ts_ms=25_000, size=1, ask_size=95)]   # one rebuild
    out = refill_half_lives(evs, 6300.0, cfg)
    assert out["n_events"] == 1 and out["n_unrecovered"] == 2


def test_refill_skips_unclassifiable_trades(cfg, trade_factory):
    """Missing quote side or dead-on-mid prints can't say which book was
    eaten — they must be skipped, not defaulted to 'buy'."""
    evs = [trade_factory(ts_ms=0, bid=None, size=50, ask_size=100),
           trade_factory(ts_ms=1, price=10.1, size=50, ask_size=100)]  # exact mid
    out = refill_half_lives(evs, 6300.0, cfg)
    assert out["n_events"] == 0 and out["n_unrecovered"] == 0


# --- purge filter -----------------------------------------------------------------


def _bins(sizes: dict) -> dict:
    return {b: {"size": v} for b, v in sizes.items()}


def test_purge_step_all_buckets_at_once(cfg):
    prev = _bins({"d00_10": 100, "d10_25": 100, "d25_40": 100, "d40_50": 100})
    cur = _bins({"d00_10": 10, "d10_25": 10, "d25_40": 10, "d40_50": 100})
    assert purge_step(prev, cur, cfg) is True                 # 3 of 4 stepped


def test_purge_not_triggered_by_one_bucket(cfg):
    prev = _bins({"d00_10": 100, "d10_25": 100, "d25_40": 100, "d40_50": 100})
    cur = _bins({"d00_10": 10, "d10_25": 100, "d25_40": 100, "d40_50": 100})
    assert purge_step(prev, cur, cfg) is False                # strike-specific


def test_purge_counts_vanished_buckets(cfg):
    """Total withdrawal (quotes pulled entirely -> bucket missing) is the
    most extreme purge, not a data blank."""
    prev = _bins({"d00_10": 100, "d10_25": 100, "d25_40": 100, "d40_50": 100})
    cur = _bins({"d40_50": 100})                              # three vanished
    assert purge_step(prev, cur, cfg) is True


def test_purge_no_previous_bin(cfg):
    assert purge_step(None, _bins({"d40_50": 10}), cfg) is False


# --- fade dial ---------------------------------------------------------------------


def _scored(z_size, z_spread=0.0, minutes_ago=0):
    return {"ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "buckets": {"d40_50": {"size_z": z_size, "spread_z": z_spread}}}


def test_scatter_needs_persistence(cfg):
    one_bin = fade_dial([_scored(-9.0)], [], False, False, cfg)
    assert one_bin["scatter"] is False                        # single blip
    two = fade_dial([_scored(-9.0, minutes_ago=1), _scored(-9.0)],
                    [], False, False, cfg)
    assert two["scatter"] is True
    assert two["scatter_buckets"] == ["d40_50"]


def test_scatter_broken_by_recovery(cfg):
    dial = fade_dial([_scored(-9.0, minutes_ago=1), _scored(-1.0)],
                     [], False, False, cfg)
    assert dial["scatter"] is False                           # it refilled


def test_scatter_needs_time_adjacency(cfg):
    """z<=-8 before a feed gap and again after it are two blips, not one
    sustained scatter."""
    dial = fade_dial([_scored(-9.0, minutes_ago=10), _scored(-9.0)],
                     [], False, False, cfg)
    assert dial["scatter"] is False


def test_purge_and_calendar_veto_scatter(cfg):
    bins = [_scored(-9.0, minutes_ago=1), _scored(-9.0)]
    assert fade_dial(bins, [], True, False, cfg)["scatter"] is False
    assert fade_dial(bins, [], False, True, cfg)["scatter"] is False
    assert fade_dial(bins, [], True, False, cfg)["veto"] is True


def test_spread_is_a_separate_dial_with_persistence(cfg):
    two = fade_dial([_scored(0.0, 9.0, minutes_ago=1), _scored(0.0, 9.0)],
                    [], False, False, cfg)
    assert two["spread_alert"] is True and two["scatter"] is False
    blip = fade_dial([_scored(0.0, 0.0, minutes_ago=1), _scored(0.0, 9.0)],
                     [], False, False, cfg)
    assert blip["spread_alert"] is False                      # one-bin blip


def test_defended_magnet_picks_fastest_refill(cfg):
    defense = [{"strike": 6300.0, "verdict": "defended", "refill_half_life_s": 30.0},
               {"strike": 6310.0, "verdict": "defended", "refill_half_life_s": 10.0},
               {"strike": 6320.0, "verdict": "abandoned", "refill_half_life_s": None}]
    dial = fade_dial([], defense, False, False, cfg)
    assert dial["defended_magnet"] == 6310.0
    assert set(dial["defense"]) == {"6300.0", "6310.0", "6320.0"}  # JSON-safe keys


def test_cold_baseline_never_scatters(cfg):
    """z=None (cold store) must read as 'no opinion', not 'calm' or 'panic'."""
    bins = [_scored(None, None, minutes_ago=1), _scored(None, None)]
    dial = fade_dial(bins, [], False, False, cfg)
    assert dial["scatter"] is False and dial["spread_alert"] is False


# --- semantic-fix regressions (domain-expert review, 07-04) ---------------------


def test_refill_price_walkaway_is_not_recovery(cfg, trade_factory):
    """Size re-posted two ticks worse is polite abandonment, not defense."""
    evs = [trade_factory(ts_ms=0, price=10.2, size=50, ask_size=100)]
    for i in range(1, 4):                       # full size back, price walked up
        evs.append(trade_factory(ts_ms=i * 15_000, price=10.45, bid=10.25,
                                 ask=10.45, size=1, ask_size=100))
    out = refill_half_lives(evs, 6300.0, cfg)
    assert out["n_events"] == 0 and out["n_unrecovered"] == 1


def test_refill_recovery_at_price_counts(cfg, trade_factory):
    """Same size back at the SAME ask (within a tick) is real defense."""
    evs = [trade_factory(ts_ms=0, price=10.2, size=50, ask_size=100),
           trade_factory(ts_ms=15_000, price=10.2, size=1, ask_size=90)]
    evs = evs * 3                                # not enough alone...
    evs = [trade_factory(ts_ms=i * 100_000, price=10.2, size=50, ask_size=100)
           for i in range(3)]
    evs += [trade_factory(ts_ms=i * 100_000 + 15_000, price=10.2, size=1,
                          ask_size=90) for i in range(3)]
    out = refill_half_lives(evs, 6300.0, cfg)
    assert out["verdict"] == "defended" and out["refill_half_life_s"] == 15.0


def test_inside_spread_print_is_not_an_eat(cfg, trade_factory):
    """A print INSIDE the quote (package leg / price improvement) consumed no
    displayed size — it must not open an eat."""
    evs = [trade_factory(ts_ms=0, price=10.15, size=50, ask_size=100)]
    out = refill_half_lives(evs, 6300.0, cfg)
    assert out["n_events"] == 0 and out["n_unrecovered"] == 0


def test_package_cluster_excluded_everywhere(cfg, trade_factory):
    """Same-ms prints across DISTINCT contracts = multi-leg package: excluded
    from both the tape sign and the eat detector."""
    legs = [trade_factory(ts_ms=5, strike=6300.0, price=10.2, size=50),
            trade_factory(ts_ms=5, strike=6350.0, price=4.2, bid=4.0, ask=4.4,
                          size=50)]
    tape = read_tape(legs, cfg)
    assert tape["tilt"] == 0.0 and tape["n_cluster_excluded"] == 2
    assert refill_half_lives(legs, 6300.0, cfg)["n_events"] == 0


def test_tape_delta_weighting(cfg, trade_factory):
    """40 wing lots at 0.05 delta must not outweigh 10 ATM lots at 0.50:
    hedge pressure is delta x contracts, not premium."""
    deltas = {"6300.0|call": 0.50, "6400.0|call": 0.05}
    trades = [trade_factory(strike=6300.0, price=10.2, size=10, ts_ms=1),   # ATM buy
              trade_factory(strike=6400.0, price=0.5, bid=0.5, ask=0.7,
                            size=40, ts_ms=2)]                              # wing sell
    out = read_tape(trades, cfg, deltas)
    assert out["tilt"] > 0                       # ATM delta-weight dominates
