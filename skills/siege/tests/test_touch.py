"""Touch window state machine: the freeze, the near-spot artifact guard, and
one-episode-per-frozen-level."""
from conftest import make_bars, run, seed_baseline


def _approach_and_touch(store):
    """Spot walks from 1σ below the 6400 call wall to a touch at 10:00."""
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0}, make_bars(579))
    return run(store, 10, 0, 6398.0, {"call_wall": 6400.0},
               make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))


def test_wall_repick_never_reanchors_open_window(store):
    _approach_and_touch(store)
    rec = run(store, 10, 5, 6402.0, {"call_wall": 6410.0}, make_bars(604))
    eps = store.read_session()["episodes"]
    assert len(eps) == 1
    assert eps[0]["level"] == 6400.0            # FROZEN at first touch
    tower = next(t for t in rec["towers"] if t["kind"] == "call_wall")
    assert tower["status"] == "engaged" and tower["level"] == 6400.0


def test_near_spot_artifact_suppresses_verdict(store):
    # tower first seen already at spot: never observed >= 0.25σ away
    run(store, 10, 0, 6398.0, {"call_wall": 6398.0},
        make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))
    run(store, 10, 15, 6398.0, {"call_wall": 6398.0}, make_bars(614))
    ep = store.read_session()["episodes"][0]
    assert ep["near_spot"] is True
    assert ep["effort_pct"] is not None         # still recorded...
    assert ep["verdict"] is None                # ...verdict suppressed


def test_repick_to_spot_resets_watch_history(store):
    # the OLD level's standing distance must not launder a spot-hugging re-pick
    run(store, 9, 40, 6360.0, {"call_wall": 6400.0}, make_bars(579))
    run(store, 10, 0, 6398.0, {"call_wall": 6398.0},
        make_bars(599, price_fn=lambda m: 639.8 if m >= 595 else 636.0))
    assert store.read_session()["episodes"][0]["near_spot"] is True


def test_one_episode_per_frozen_level(store):
    seed_baseline(store, 21, lambda i: 100 * (i + 1))
    _approach_and_touch(store)
    run(store, 10, 15, 6402.0, {"call_wall": 6400.0}, make_bars(614))
    run(store, 10, 45, 6405.0, {"call_wall": 6400.0},   # grades -> resolved
        make_bars(644, price_fn=lambda m: 640.5 if m >= 630 else 636.0))
    run(store, 10, 50, 6398.0, {"call_wall": 6400.0}, make_bars(649))
    assert len(store.read_session()["episodes"]) == 1   # no re-open, same level
