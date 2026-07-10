"""engine — composition, regime mapping, the report card, promotion math."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lob_flow.baselines import BaselineStore
from lob_flow.contracts import BlockInfo, LobConfig
from lob_flow.engine import (confidence_modifier, evaluate_once, grade_day,
                             promotion_check, wilson_bounds)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)
GEX = {"magnet": 6300.0, "call_wall": 6350.0, "put_wall": 6250.0,
       "gamma_flip": 6280.0, "sigma": 40.0}
CALM = BlockInfo(blocked=False)


def _bins(size, n=2, spread=0.15):
    buckets = {b: {"size": size, "spread": spread, "n": 20}
               for b in ("d00_10", "d10_25", "d25_40", "d40_50")}
    return [{"ts": (NOW - timedelta(minutes=n - 1 - i)).isoformat(),
             "buckets": buckets, "revisions_per_s": 2.0} for i in range(n)]


def _stores(tmp_path, cfg, store_seeder, chain_key_builder, days=21):
    kv = {}
    for m, v in (("size", 100.0), ("spread", 0.15)):
        for offset in (0, 1):                          # both bins' tod slots
            kv.update(chain_key_builder(cfg, NOW - timedelta(minutes=offset), m, v))
    kv.update(chain_key_builder(cfg, NOW, "noise", 2.0, buckets=["chain"]))
    lob = store_seeder(tmp_path / "lob.json", cfg, NOW, kv, days=days)
    spy_kv = {}
    for m, v in (("size", 500.0), ("spread", 0.01)):
        spy_kv.update(chain_key_builder(cfg, NOW, m, v, buckets=["spy"]))
    spy = store_seeder(tmp_path / "spy.json", cfg, NOW, spy_kv, days=days)
    return lob, spy


def _evaluate(cfg, lob_store, spy_store, *, lob_bins, trades=(),
              spy_bins=(), block=CALM, gex=GEX, vix=None):
    return evaluate_once(NOW, cfg, lob_bins=lob_bins, tape_trades=list(trades),
                         gex_strikes=gex, spy_bins=list(spy_bins),
                         lob_store=lob_store, spy_store=spy_store,
                         cal_block=block, vix=vix)


def test_calm_day_no_regime(tmp_path, cfg, store_seeder, chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(100.0))
    e = r["lob_flow"]
    assert e["regime"] is None and e["scatter"] is False
    assert e["baseline_label"] == "robust"
    assert r["baseline_rows"]["lob_flow"], "calm folds still feed the memory"


def test_scatter_maps_to_short_gamma_with_gravity_magnet(
        tmp_path, cfg, store_seeder, chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(2.0))          # sizes collapsed
    e = r["lob_flow"]
    assert e["scatter"] is True and e["regime"] == "short_gamma"
    assert e["magnet"] == GEX["magnet"] and e["magnet_source"] == "gravity"


def test_defense_maps_to_long_gamma_with_own_magnet(
        tmp_path, cfg, store_seeder, chain_key_builder, trade_factory):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    trades = []
    t0 = int(NOW.timestamp() * 1000) - 600_000
    for i in range(4):                                          # fast refills @ magnet
        trades.append(trade_factory(ts_ms=t0 + i * 100_000, strike=6300.0,
                                    price=10.2, size=50, ask_size=100))
        trades.append(trade_factory(ts_ms=t0 + i * 100_000 + 15_000,
                                    strike=6300.0, price=10.2, size=1,
                                    ask_size=90))
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(100.0), trades=trades)
    e = r["lob_flow"]
    assert e["regime"] == "long_gamma"
    assert e["magnet"] == 6300.0 and e["magnet_source"] == "defense"
    assert e["defense"]["6300.0"]["verdict"] == "defended"    # JSON-safe keys


def test_purge_vetoes_scatter(tmp_path, cfg, store_seeder, chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    healthy = _bins(100.0, n=2)[0]                              # NOW-1m, sizes 100
    crashed = _bins(2.0, n=1)[0]                                # NOW, sizes 2: a STEP
    r = _evaluate(cfg, lob, spy, lob_bins=[healthy, crashed])
    e = r["lob_flow"]
    assert e["purge_flag"] is True
    assert e["scatter"] is False and e["regime"] is None        # veto, not signal
    assert r["purge_until"] is not None                         # horizon handed back


def test_purge_horizon_keeps_vetoing(tmp_path, cfg, store_seeder,
                                     chain_key_builder):
    """One fold later: sizes still collapsed but no NEW step — the sticky
    horizon must keep the veto up (a mass-cancel is not fear a minute later)."""
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(2.0))           # no step, low sizes
    assert r["lob_flow"]["scatter"] is True                     # without horizon...
    r2 = evaluate_once(NOW, cfg, lob_bins=_bins(2.0), tape_trades=[],
                       gex_strikes=GEX, spy_bins=[], lob_store=lob,
                       spy_store=spy, cal_block=CALM, vix=None,
                       purge_until=NOW + timedelta(minutes=5))  # ...with it:
    assert r2["lob_flow"]["scatter"] is False
    assert r2["lob_flow"]["purge_flag"] is True


def test_stale_bins_read_as_no_opinion(tmp_path, cfg, store_seeder,
                                       chain_key_builder):
    """Bins from a feed that died 10 minutes ago are dropped at the door."""
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    old = [dict(b, ts=(NOW - timedelta(minutes=10 + i)).isoformat())
           for i, b in enumerate(_bins(2.0))]
    r = _evaluate(cfg, lob, spy, lob_bins=old)
    e = r["lob_flow"]
    assert e["scatter"] is False and e["regime"] is None and e["buckets"] == {}


def test_calendar_block_vetoes(tmp_path, cfg, store_seeder, chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    blocked = BlockInfo(blocked=True, kind="FOMC")
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(2.0), block=blocked)
    assert r["lob_flow"]["scatter"] is False
    assert r["lob_flow"]["calendar_blocked"] is True


def test_cold_store_gives_no_opinion(tmp_path, cfg, store_seeder,
                                     chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder, days=3)
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(2.0))           # crash, but cold
    e = r["lob_flow"]
    assert e["scatter"] is False and e["baseline_label"] == "cold"


def test_layer_views_shape_and_shadow_weight(tmp_path, cfg, store_seeder,
                                             chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(100.0))
    for v in r["layer_views"]:
        assert v.weight == 0.0                                  # shadow, always
        assert 0.0 <= v.confidence <= 1.0
    assert {v.layer for v in r["layer_views"]} == {"lob_flow", "spy_depth"}


def test_spy_control_scores_and_borrows_magnet(tmp_path, cfg, store_seeder,
                                               chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    spy_bins = [{"ts": NOW.isoformat(), "size": 500.0, "spread": 0.01}]
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(100.0), spy_bins=spy_bins)
    s = r["spy_depth"]
    assert s["scatter"] is False
    assert r["baseline_rows"]["spy_depth"], "control feeds its own store"


def test_missing_gex_context_degrades(tmp_path, cfg, store_seeder,
                                      chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(2.0), gex=None)
    e = r["lob_flow"]
    assert e["scatter"] is True and e["magnet"] is None         # nothing to borrow


# --- report card ---------------------------------------------------------------


def _diary_row(ts, engine, sigma=40.0, **eng):
    return {"ts": ts.isoformat(), "sigma": sigma, engine: eng}


def _flat_bars(start, minutes, level=6300.0, width=1.0):
    return [{"ts": (start + timedelta(minutes=i)).isoformat(),
             "high": level + width, "low": level - width} for i in range(minutes)]


def test_grade_scatter_hit_and_miss(cfg):
    rows = [_diary_row(NOW, "lob_flow", scatter=True)]
    wild = _flat_bars(NOW, 15, 6300, 1) + _flat_bars(NOW + timedelta(minutes=15),
                                                     15, 6330, 1)
    hit = grade_day("2026-07-07", rows, wild, cfg)
    assert hit["engines"]["lob_flow"]["scatter"] == {
        "n": 1, "hits": 1, "rate": 1.0,
        "wilson_lo": hit["engines"]["lob_flow"]["scatter"]["wilson_lo"],
        "wilson_hi": hit["engines"]["lob_flow"]["scatter"]["wilson_hi"]}
    quiet = _flat_bars(NOW, 40, 6300, 1)                        # range ~2 < 20
    miss = grade_day("2026-07-07", rows, quiet, cfg)
    assert miss["engines"]["lob_flow"]["scatter"]["hits"] == 0


def test_grade_defense_pin_hold(cfg):
    rows = [_diary_row(NOW, "lob_flow", regime="long_gamma",
                       magnet_source="defense", magnet=6300.0)]
    held = _flat_bars(NOW, 75, 6302, 1)                         # near the strike
    assert grade_day("d", rows, held, cfg)["engines"]["lob_flow"]["defense"]["hits"] == 1
    ran = _flat_bars(NOW, 40, 6300, 1) + _flat_bars(NOW + timedelta(minutes=40),
                                                    35, 6400, 1)
    assert grade_day("d", rows, ran, cfg)["engines"]["lob_flow"]["defense"]["hits"] == 0


def test_grade_episodes_dedupe_consecutive_calls(cfg):
    rows = [_diary_row(NOW + timedelta(minutes=i), "lob_flow", scatter=True)
            for i in range(5)]                                  # one idea, 5 scans
    wild = _flat_bars(NOW, 60, 6330, 30)
    card = grade_day("d", rows, wild, cfg)
    assert card["engines"]["lob_flow"]["scatter"]["n"] == 1


def test_grade_no_bars_no_grades(cfg):
    rows = [_diary_row(NOW, "lob_flow", scatter=True)]
    card = grade_day("d", rows, [], cfg)
    assert card["engines"]["lob_flow"]["scatter"]["n"] == 0


# --- promotion math (dormant) -----------------------------------------------------


def _hist(lob=(0, 0), spy=(0, 0), gex=(0, 0)):
    def _o(h, n):
        return {"overall": {"n": n, "current_hit_rate": (h / n if n else 0)}}
    return [{"engines": {"lob_flow": _o(*lob), "spy_depth": _o(*spy),
                         "gex_views": _o(*gex)}}]


def test_promotion_fails_thin_record(cfg):
    ok, reason = promotion_check(_hist(lob=(20, 25), spy=(10, 30), gex=(15, 30)), cfg)
    assert not ok and "too thin" in reason


def test_promotion_fails_when_not_beating_control(cfg):
    ok, reason = promotion_check(
        _hist(lob=(60, 100), spy=(58, 100), gex=(30, 100)), cfg)
    assert not ok and "control" in reason


def test_promotion_passes_only_on_clear_daylight(cfg):
    ok, reason = promotion_check(
        _hist(lob=(90, 100), spy=(40, 100), gex=(40, 100)), cfg)
    assert ok and "beats" in reason


def test_promotion_fail_closed_on_garbage(cfg):
    ok, _ = promotion_check([{"engines": None}, {}, {"engines": {"lob_flow": {}}}], cfg)
    assert not ok


def test_confidence_modifier_clamp_and_dormancy(cfg):
    assert confidence_modifier(False, True, True, cfg) == 1.0     # dormant
    assert confidence_modifier(True, True, False, cfg) == cfg.confidence_clamp[0]
    assert confidence_modifier(True, False, True, cfg) == cfg.confidence_clamp[1]
    assert confidence_modifier(True, False, False, cfg) == 1.0
    lo, hi = cfg.confidence_clamp
    assert 0.8 <= lo < 1.0 < hi <= 1.2                            # a nudge, never a veto


def test_wilson_shrinks_with_evidence():
    lo_thin, hi_thin = wilson_bounds(6, 4)
    lo_fat, hi_fat = wilson_bounds(60, 40)
    assert (hi_fat - lo_fat) < (hi_thin - lo_thin)
    assert wilson_bounds(0, 0) == (0.0, 1.0)


# --- semantic-fix regressions (domain-expert review, 07-04) ---------------------


def test_spread_blowout_cotriggers_turbulence(tmp_path, cfg, store_seeder,
                                              chain_key_builder):
    """SPX LMMs widen at mandated minimum size at least as often as they
    pull it — persistent spread breach must reach the regime, not just a log."""
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    bins = _bins(100.0, spread=3.0)                     # sizes normal, spreads blown
    r = _evaluate(cfg, lob, spy, lob_bins=bins)
    e = r["lob_flow"]
    assert e["spread_alert"] is True and e["scatter"] is False
    assert e["turbulence"] is True and e["regime"] == "short_gamma"


def test_purge_requires_spreads_normal(tmp_path, cfg, store_seeder,
                                       chain_key_builder):
    """A genuine panic blows spreads out WHILE size steps — that must stay a
    scatter/turbulence signal, never get eaten by the purge veto."""
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    healthy = _bins(100.0, n=2)[0]
    panic = _bins(2.0, n=1, spread=3.0)[0]              # step AND spread blowout
    r = _evaluate(cfg, lob, spy, lob_bins=[healthy, panic])
    assert r["lob_flow"]["purge_flag"] is False         # not classified mechanical


def test_purge_early_lift_on_recovery(tmp_path, cfg, store_seeder,
                                      chain_key_builder):
    """The book visibly re-formed -> the sticky veto lifts early."""
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    r = evaluate_once(NOW, cfg, lob_bins=_bins(100.0), tape_trades=[],
                      gex_strikes=GEX, spy_bins=[], lob_store=lob,
                      spy_store=spy, cal_block=CALM, vix=None,
                      purge_until=NOW + timedelta(minutes=8))
    assert r["purge_until"] is None                     # recovered -> lifted
    assert r["lob_flow"]["purge_flag"] is False


def test_defense_suppressed_late_session(tmp_path, cfg, store_seeder,
                                         chain_key_builder, trade_factory):
    """Post-15:15 charm/pin mechanics: defense is recorded but asserts no
    regime (and +60m grading would self-censor anyway)."""
    late = NOW.replace(hour=15, minute=30)
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    trades = []
    t0 = int(late.timestamp() * 1000) - 600_000
    for i in range(4):
        trades.append(trade_factory(ts_ms=t0 + i * 100_000, strike=6300.0,
                                    price=10.2, size=50, ask_size=100))
        trades.append(trade_factory(ts_ms=t0 + i * 100_000 + 15_000,
                                    strike=6300.0, price=10.2, size=1,
                                    ask_size=90))
    kv = {}
    for m, v in (("size", 100.0), ("spread", 0.15)):
        kv.update(chain_key_builder(cfg, late, m, v))
    lob2 = store_seeder(tmp_path / "lob2.json", cfg, late, kv)
    bins = [{"ts": (late - timedelta(minutes=1 - i)).isoformat(),
             "buckets": {b: {"size": 100.0, "spread": 0.15, "n": 20}
                         for b in ("d00_10", "d40_50")},
             "revisions_per_s": 2.0} for i in range(2)]
    r = evaluate_once(late, cfg, lob_bins=bins, tape_trades=trades,
                      gex_strikes=GEX, spy_bins=[], lob_store=lob2,
                      spy_store=spy, cal_block=CALM, vix=None)
    e = r["lob_flow"]
    assert e["late_session"] is True and e["regime"] is None
    assert e["defense"]["6300.0"]["verdict"] == "defended"   # still recorded


def test_gravity_strikes_snap_to_grid(tmp_path, cfg, store_seeder,
                                      chain_key_builder, trade_factory):
    """A 6302.5 gamma-centroid magnet must still find the 6300 trades."""
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    gex = dict(GEX, magnet=6302.5)
    trades = []
    t0 = int(NOW.timestamp() * 1000) - 600_000
    for i in range(4):
        trades.append(trade_factory(ts_ms=t0 + i * 100_000, strike=6300.0,
                                    price=10.2, size=50, ask_size=100))
        trades.append(trade_factory(ts_ms=t0 + i * 100_000 + 15_000,
                                    strike=6300.0, price=10.2, size=1,
                                    ask_size=90))
    r = _evaluate(cfg, lob, spy, lob_bins=_bins(100.0), trades=trades, gex=gex)
    assert r["lob_flow"]["magnet"] == 6300.0            # snapped, found, defended


def test_fed_until_prevents_refeeding_a_stalled_bin(tmp_path, cfg, store_seeder,
                                                    chain_key_builder):
    lob, spy = _stores(tmp_path, cfg, store_seeder, chain_key_builder)
    bins = _bins(100.0)
    r1 = _evaluate(cfg, lob, spy, lob_bins=bins)
    assert r1["baseline_rows"]["lob_flow"]
    r2 = evaluate_once(NOW, cfg, lob_bins=bins, tape_trades=[],
                       gex_strikes=GEX, spy_bins=[], lob_store=lob,
                       spy_store=spy, cal_block=CALM, vix=None,
                       fed_until=r1["fed_until"])
    assert r2["baseline_rows"]["lob_flow"] == []        # same bins: fed once
