"""daemon — focus set, pruning, replay, staleness, fold_once (no network)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lob_flow.baselines import BaselineStore, FileCalendar
from lob_flow.contracts import DepthSample
from lob_flow.daemon import (Adapters, _Mem, _focus_contracts, _fold_spy_bin,
                             _past_close, _prune_trades, _replay_today,
                             _rev_per_s, fold_once)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 7, 10, 30, tzinfo=ET)


class FakeGex:
    def __init__(self, strikes=None):
        self._s = strikes

    def strikes(self, ticker):
        return self._s


def _adapters(sd, tmp_path, gex=None, clock=None, vix=lambda: 18.0):
    return Adapters(quote_feed=None, tape_feed=None, schwab_stream=None,
                    gex=FakeGex(gex), calendar=FileCalendar(tmp_path / "cal.json"),
                    clock=clock or (lambda: NOW), vix=vix, state=sd)


def test_focus_contracts_atm_window_plus_gravity(sd, tmp_path):
    a = _adapters(sd, tmp_path, gex={"magnet": 6300.0, "call_wall": 6420.0,
                                     "put_wall": 6180.0})
    mem = _Mem()
    mem.spot = 6301.0
    focus = _focus_contracts(a2cfg(), a, mem)
    strikes = {s for s, _ in focus}
    assert 6300.0 in strikes and 6250.0 in strikes and 6350.0 in strikes  # ATM +/-10
    assert 6420.0 in strikes and 6180.0 in strikes                        # gravity
    assert len(focus) == len(set(focus))                                  # deduped
    assert all(r in ("call", "put") for _, r in focus)


def test_focus_contracts_without_spot_still_covers_gravity(sd, tmp_path):
    a = _adapters(sd, tmp_path, gex={"magnet": 6300.0})
    focus = _focus_contracts(a2cfg(), a, _Mem())
    assert (6300.0, "call") in focus and (6300.0, "put") in focus


def a2cfg():
    from lob_flow.contracts import LobConfig
    return LobConfig()


def test_prune_trades(trade_factory):
    mem = _Mem()
    old = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
    fresh = int((NOW - timedelta(minutes=5)).timestamp() * 1000)
    mem.trades.extend([trade_factory(ts_ms=old), trade_factory(ts_ms=fresh)])
    _prune_trades(mem, NOW)
    assert len(mem.trades) == 1 and mem.trades[0].ts_ms == fresh


def test_fold_spy_bin_medians():
    mem = _Mem()
    for bs, asz, b, a in ((10, 10, 100.0, 100.02), (20, 20, 100.0, 100.04),
                          (30, 30, 100.0, 100.06)):
        mem.spy_samples.append(DepthSample(ts=NOW.isoformat(), bid=b, ask=a,
                                           bid_size=bs, ask_size=asz))
    bins = _fold_spy_bin(mem, NOW)
    assert bins[0]["size"] == 40 and abs(bins[0]["spread"] - 0.04) < 1e-9
    assert _fold_spy_bin(_Mem(), NOW) == []


def test_rev_per_s_prefers_stream_and_uses_real_elapsed(cfg):
    mem = _Mem()
    mem.stream_up = True
    mem.option_revisions = 120
    assert _rev_per_s(5, mem, NOW, cfg, True) == 2.0      # first window: nominal 60s
    assert mem.option_revisions == 0                      # window reset
    mem.option_revisions = 120
    later = NOW + timedelta(seconds=120)                  # stretched real window
    assert _rev_per_s(5, mem, later, cfg, True) == 1.0    # 120 / 120s, not /60
    mem2 = _Mem()
    assert _rev_per_s(30, mem2, NOW, cfg, True) == 0.5    # sweep-diff proxy
    assert _rev_per_s(30, _Mem(), NOW, cfg, False) is None  # first sweep: unknowable


def test_replay_today_reseeds_bins_skips_gaps(sd):
    day = NOW.date().isoformat()
    for i in range(12):
        sd.append(sd.raw(day, "sweeps"), {"ts": f"t{i}", "buckets": {}})
    sd.gap_marker(day, "sweeps", NOW, "x")
    mem = _Mem()
    _replay_today(sd, mem, NOW)
    assert 0 < len(mem.sweep_bins) <= 10
    assert all(not b.get("gap") for b in mem.sweep_bins)


def test_past_close(cfg):
    assert not _past_close(NOW, cfg)
    assert _past_close(NOW.replace(hour=16, minute=6), cfg)


def test_fold_once_writes_latest_agg_health(sd, tmp_path, cfg, trade_factory):
    a = _adapters(sd, tmp_path, gex={"magnet": 6300.0, "sigma": 40.0})
    mem = _Mem()
    mem.spot = 6301.0
    buckets = {b: {"size": 100.0, "spread": 0.15, "n": 20}
               for b in ("d00_10", "d40_50")}
    mem.sweep_bins.append({"ts": NOW.isoformat(), "buckets": buckets,
                           "revisions_per_s": 2.0})
    mem.trades.append(trade_factory(ts_ms=int(NOW.timestamp() * 1000)))
    lob = BaselineStore(sd.baseline("lob_flow"), cfg)
    spy = BaselineStore(sd.baseline("spy_depth"), cfg)
    result = fold_once(cfg, a, mem, lob, spy)
    assert result["lob_flow"]["source"] == "lob_flow"
    latest = sd.read_latest(NOW, 600)
    assert latest and "lob_flow" in latest and "spy_depth" in latest
    assert latest["layer_views"][0]["weight"] == 0.0
    agg = list(sd.rows(sd.agg(NOW.date().isoformat())))
    assert {r["engine"] for r in agg} == {"lob_flow", "spy_depth"}
    assert sd.read_latest(NOW + timedelta(seconds=700), 600) is None  # staleness


def test_fold_once_survives_vix_failure(sd, tmp_path, cfg):
    def boom():
        raise RuntimeError("vix down")
    a = _adapters(sd, tmp_path, vix=boom)
    mem = _Mem()
    fold_once(cfg, a, mem, BaselineStore(sd.baseline("lob_flow"), cfg),
              BaselineStore(sd.baseline("spy_depth"), cfg))
    assert sd.read_latest(NOW, 600) is not None           # still folded


def test_fold_once_stale_windows_read_as_no_opinion(sd, tmp_path, cfg,
                                                    trade_factory):
    """THE fail-closed case: feeds died 20 minutes ago, the fold keeps
    running — the published view must hold NO opinion (empty buckets, no
    regime), and the frozen SPY window must yield no control bin."""
    a = _adapters(sd, tmp_path, gex={"magnet": 6300.0, "sigma": 40.0})
    mem = _Mem()
    old = NOW - timedelta(minutes=20)
    buckets = {b: {"size": 2.0, "spread": 3.0, "n": 20}
               for b in ("d00_10", "d40_50")}
    mem.sweep_bins.append({"ts": old.isoformat(), "buckets": buckets,
                           "revisions_per_s": 2.0})
    mem.trades.append(trade_factory(ts_ms=int(old.timestamp() * 1000)))
    mem.spy_samples.append(DepthSample(ts=old.isoformat(), bid=100.0,
                                       ask=100.02, bid_size=10, ask_size=10))
    result = fold_once(cfg, a, mem, BaselineStore(sd.baseline("lob_flow"), cfg),
                       BaselineStore(sd.baseline("spy_depth"), cfg))
    e = result["lob_flow"]
    assert e["regime"] is None and e["scatter"] is False and e["buckets"] == {}
    assert result["spy_depth"]["regime"] is None
    assert len(mem.trades) == 0                           # pruned at the fold
    assert len(mem.spy_samples) == 0                      # pruned at the fold


def test_fold_once_carries_purge_horizon(sd, tmp_path, cfg):
    """A purge veto must OUTLIVE its bin: the horizon rides mem between
    folds, so the next fold (sizes still low, no new step) stays vetoed."""
    a = _adapters(sd, tmp_path, gex={"magnet": 6300.0, "sigma": 40.0})
    mem = _Mem()
    mem.purge_until = NOW + timedelta(minutes=5)          # set by a prior fold
    buckets = {b: {"size": 2.0, "spread": 0.15, "n": 20}
               for b in ("d00_10", "d40_50")}
    mem.sweep_bins.append({"ts": (NOW - timedelta(minutes=1)).isoformat(),
                           "buckets": buckets, "revisions_per_s": 2.0})
    mem.sweep_bins.append({"ts": NOW.isoformat(), "buckets": buckets,
                           "revisions_per_s": 2.0})
    result = fold_once(cfg, a, mem, BaselineStore(sd.baseline("lob_flow"), cfg),
                       BaselineStore(sd.baseline("spy_depth"), cfg))
    assert result["lob_flow"]["purge_flag"] is True       # sticky veto
    assert mem.purge_until is not None                    # horizon preserved


def test_replay_today_reseeds_tape_window(sd, trade_factory):
    day = NOW.date().isoformat()
    fresh = trade_factory(ts_ms=int((NOW - timedelta(minutes=5)).timestamp() * 1000))
    stale = trade_factory(ts_ms=int((NOW - timedelta(minutes=40)).timestamp() * 1000))
    for e in (stale, fresh):
        sd.append(sd.raw(day, "tape"), e.__dict__)
    sd.gap_marker(day, "tape", NOW, "x")
    mem = _Mem()
    _replay_today(sd, mem, NOW)
    assert [t.ts_ms for t in mem.trades] == [fresh.ts_ms]  # window only, no gaps


def test_spxw_symbol_format():
    from lob_flow.spy_stream import spxw_symbol
    assert spxw_symbol("2026-07-06", "call", 7475.0) == "SPXW  260706C07475000"
    assert spxw_symbol("2026-07-06", "put", 6302.5) == "SPXW  260706P06302500"
    assert len(spxw_symbol("2026-12-31", "call", 5.0)) == 21
