"""Engine coverage: reversed-sign verdicts, outcome grading both directions,
the frozen ratio, saturation, the FEED-LOST union guard, and the rollover
flush."""
import json

from conftest import DAY, make_bars, run, seed_baseline


def _drive_call_wall(store, window_vol, grade_price):
    """Approach the 6400 call wall, touch at 10:00, verdict at 10:15, grade
    at 10:45 (grade minute = 10:40). Ratio pins at 10.0 at the freeze."""
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0}, make_bars(579))
    run(store, 10, 0, 6398.0, {"call_wall": 6400.0},
        make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))
    vol_fn = (lambda m: window_vol if 590 <= m <= 610 else 1000)
    rec = run(store, 10, 15, 6402.0, {"call_wall": 6400.0},
              make_bars(614, vol_fn=vol_fn))
    run(store, 10, 45, 6405.0, {"call_wall": 6400.0},
        make_bars(644, vol_fn=vol_fn,
                  price_fn=lambda m: grade_price if m >= 630 else 636.0))
    return rec, store.read_cards(DAY)


# ---------------------------------------------------------------------------
# THE REVERSED SIGN: heavy effort -> SIEGE -> expect BREAK; quiet -> HOLD
# ---------------------------------------------------------------------------


def test_siege_verdict_grades_break(store):
    # trailing sums 2100..44100; a 105000 window is the 100th percentile
    seed_baseline(store, 21, lambda i: 100 * (i + 1))
    rec, cards = _drive_call_wall(store, window_vol=5000, grade_price=640.5)
    tower = next(t for t in rec["towers"] if t["kind"] == "call_wall")
    assert tower["verdict"] == "SIEGE"          # heavy volume at the touch...
    assert len(cards) == 1
    card = cards[0]
    assert card["verdict"] == "SIEGE" and card["outcome"] == "BREAK"  # ...breaks
    assert card["effort_basis"] == "trailing_median"
    assert card["level_frozen"] == 6400.0
    assert abs(card["move_after_30m_pts"] - 5.0) < 0.1
    assert card["near_spot"] is False and card["degraded"] is False


def test_quiet_verdict_grades_hold(store):
    seed_baseline(store, 21, lambda i: 100 * (i + 1))
    rec, cards = _drive_call_wall(store, window_vol=50, grade_price=639.5)
    tower = next(t for t in rec["towers"] if t["kind"] == "call_wall")
    assert tower["verdict"] == "QUIET"          # quiet touch...
    assert cards[0]["outcome"] == "HOLD"        # ...holds


def test_neutral_verdict_in_middle_third(store):
    # 21000 window vs sums 2100..44100 -> 10/21 = 47.6th percentile
    seed_baseline(store, 21, lambda i: 100 * (i + 1))
    rec, _ = _drive_call_wall(store, window_vol=1000, grade_price=636.0)
    tower = next(t for t in rec["towers"] if t["kind"] == "call_wall")
    assert tower["verdict"] == "NEUTRAL"


def test_put_wall_breaks_downward(store):
    seed_baseline(store, 21, lambda i: 100 * (i + 1))
    run(store, 9, 40, 6340.0, {"put_wall": 6300.0}, make_bars(579, price=634.0))
    run(store, 10, 0, 6302.0, {"put_wall": 6300.0},
        make_bars(599, price_fn=lambda m: 630.2 if m >= 595 else 634.0))
    run(store, 10, 15, 6298.0, {"put_wall": 6300.0},
        make_bars(614, price=630.0,
                  vol_fn=lambda m: 5000 if 590 <= m <= 610 else 1000))
    run(store, 10, 45, 6295.0, {"put_wall": 6300.0},
        make_bars(644, price_fn=lambda m: 629.5 if m >= 630 else 634.0))
    card = store.read_cards(DAY)[0]
    assert card["verdict"] == "SIEGE" and card["outcome"] == "BREAK"
    assert card["move_after_30m_pts"] < 0       # broke DOWN through the wall


def test_grading_uses_frozen_ratio_not_current(store):
    seed_baseline(store, 21, lambda i: 100 * (i + 1))
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0}, make_bars(579))
    run(store, 10, 0, 6398.0, {"call_wall": 6400.0},
        make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))
    run(store, 10, 15, 6402.0, {"call_wall": 6400.0}, make_bars(614))
    # grade scan hands in a DRIFTED basis (spot 6290 / close 640.5 -> ~9.82);
    # 640.5 x the FROZEN 10.0 = 6405 = BREAK. The drifted ratio would grade HOLD.
    run(store, 10, 45, 6290.0, {"call_wall": 6400.0},
        make_bars(644, price_fn=lambda m: 640.5 if m >= 630 else 636.0))
    card = store.read_cards(DAY)[0]
    assert round(card["ratio_used"], 4) == 10.0
    assert card["outcome"] == "BREAK"


# ---------------------------------------------------------------------------
# Coverage self-invalidation
# ---------------------------------------------------------------------------


def test_saturation_suppresses_new_verdicts(store):
    towers = {"magnet": 6390.0, "call_wall": 6400.0}
    run(store, 9, 40, 6360.0, towers, make_bars(579))
    run(store, 9, 45, 6390.5, towers, make_bars(584))       # magnet window opens
    # at 10:00 the magnet window alone covers 21/31 elapsed minutes (68%)
    rec = run(store, 10, 0, 6398.0, towers,
              make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))
    assert rec["saturated"] is True
    run(store, 10, 15, 6398.0, towers, make_bars(614))
    eps = {e["kind"]: e for e in store.read_session()["episodes"]}
    assert eps["call_wall"]["suppressed"] is True
    assert eps["call_wall"]["verdict"] is None              # suppressed...
    assert eps["call_wall"]["effort_pct"] is not None       # ...still recorded
    assert eps["magnet"]["verdict"] is not None             # pre-saturation window


# ---------------------------------------------------------------------------
# FEED-LOST union guard — all four dead-tape shapes keep last state
# ---------------------------------------------------------------------------


def _watching(store):
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0}, make_bars(579))


def _latest(store):
    return json.loads(store.latest.read_text())


def test_feed_lost_no_bars(store):
    _watching(store)
    rec = run(store, 10, 0, 6398.0, {"call_wall": 6400.0}, [])
    assert rec["health"] == "FEED-LOST"
    assert store.read_session()["episodes"] == []           # touch NOT taken
    assert round(_latest(store)["spx_spy_ratio"], 4) == 10.0  # frozen carry


def test_feed_degraded_stale_bars(store):
    _watching(store)
    rec = run(store, 10, 0, 6398.0, {"call_wall": 6400.0}, make_bars(594))
    assert rec["health"] == "DEGRADED"
    assert "stale" in _latest(store)["health"]["reason"]
    assert store.read_session()["episodes"] == []
    assert round(_latest(store)["spx_spy_ratio"], 4) == 10.0  # frozen carry


def test_feed_degraded_zero_volume_run(store):
    _watching(store)
    rec = run(store, 10, 0, 6398.0, {"call_wall": 6400.0},
              make_bars(599, vol_fn=lambda m: 0 if m >= 597 else 1000))
    assert rec["health"] == "DEGRADED"
    assert "zero-volume" in _latest(store)["health"]["reason"]
    assert store.read_session()["episodes"] == []


def test_feed_degraded_flat_ohlc_with_volume(store):
    _watching(store)
    rec = run(store, 10, 0, 6398.0, {"call_wall": 6400.0},
              make_bars(599, flat=True))
    assert rec["health"] == "DEGRADED"
    assert "flat-OHLC" in _latest(store)["health"]["reason"]
    assert store.read_session()["episodes"] == []


# ---------------------------------------------------------------------------
# Baseline fallback + rollover
# ---------------------------------------------------------------------------


def test_cold_baseline_falls_back_to_same_day_basis(store):
    rec, cards = _drive_call_wall(store, window_vol=5000, grade_price=640.5)
    assert rec["baseline"] == "cold"
    assert cards[0]["effort_basis"] == "same_day_pct"
    assert cards[0]["verdict"] == "SIEGE"       # window minutes top the day


def test_session_rollover_flushes_unresolved(store):
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0}, make_bars(579))
    run(store, 10, 0, 6398.0, {"call_wall": 6400.0},
        make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0},
        make_bars(579, day="2026-07-08"), day="2026-07-08")
    old = store.read_cards(DAY)
    assert len(old) == 1
    assert old[0]["outcome"] == "UNRESOLVED" and old[0]["degraded"] is True
    st = store.read_session()
    assert st["session"] == "2026-07-08" and st["episodes"] == []
