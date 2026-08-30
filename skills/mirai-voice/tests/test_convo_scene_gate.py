"""The voice sidecar's scene gate, pinned against a REAL build_scene payload.

Why this file exists, in one sentence: every read `_SceneGate.block` makes into
the scene fails silently. A renamed key does not raise here — it yields `None`,
`sig` compares equal to the last `sig` forever, and the gate answers "unchanged
since last turn" for the rest of the session while the board moves underneath
it. There is no crash, no log line and no wrong number on a screen; the desk
simply goes quiet and stays quiet. sr-7 renamed all four of those keys
(`price.now` -> `price.live_spot`, and `magnet.top_strikes` from
`[[strike, share]]` pairs to `[{"strike": ...}]` objects), so the failure mode
was one careless edit away from being live.

Everything here is built through `sndk_read.build_scene` itself rather than from
a hand-written dict. A fixture typed out by hand pins the shape someone THOUGHT
the scene had, which is exactly the assumption that breaks.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SNDK = Path(__file__).resolve().parents[2] / "sndk-pro"
sys.path.insert(0, str(_SNDK))

import sndk_read as SR          # noqa: E402
import convo                    # noqa: E402

ET = SR._ET


def _row(t, spot, *, book_at=None, chain_spot=None):
    """A diary row rich enough for build_scene to fill every gated block."""
    return {
        "ticker": "SNDK", "ts": t.isoformat(), "spot": spot, "sigma": 40.0,
        "prior_close": 1480.0, "vwap": spot - 3.0,
        "gamma_sign": "positive", "regime": "neutral",
        "gamma_flip": spot - 20.0,
        "profile_ladder": {"ct": spot - 10.0, "pt": spot - 30.0,
                           "state": "positive"},
        "gex_views": {
            "mass_by_strike": [[1500.0, 9.0], [1550.0, 5.0], [1450.0, 3.0]],
            "net_by_strike": [[1500.0, 9e8], [1550.0, 4e8], [1450.0, -5e8]],
            "front_dte": 2,
            "shove": {"shove_up_margin": 3.0, "shove_down_margin": 1.0},
        },
        "dex_views": {"net_dex_total": 3.4e9},
        "flows_front": {"cex": 2.6e8, "charm_wall": 1530.0, "vex": 8.2e6},
        "meta": {"spot_source": "schwab_quote",
                 "chain_spot": spot if chain_spot is None else chain_spot,
                 "book_asof": (book_at or t).isoformat(),
                 "book_source": "pull", "cache_age_s": 0.0,
                 "expiries": [{"date": "2026-08-21", "dte": 2}]},
    }


@pytest.fixture
def tape(tmp_path, monkeypatch):
    """A real half-hour of rows, written where the reader looks for them."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    day = datetime(2026, 8, 19, 13, 1, tzinfo=ET)
    d = tmp_path / "sndk_reversion"
    d.mkdir(parents=True)
    import json
    rows = [_row(day - timedelta(minutes=2 * i), 1500.0 + i) for i in range(15)][::-1]
    (d / "2026-08-19.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "sndk_reads").mkdir(parents=True)
    return day


def test_the_gate_reads_a_real_scene_and_finds_all_four_values(tape):
    """The whole point. If any of the four moves, these come back None and the
    gate silently welds itself shut — so they are asserted as VALUES, not as
    'no exception was raised'."""
    scene = convo.build_voice_scene(tape)
    assert scene is not None

    spot = (scene.get("price") or {}).get("live_spot")
    regime = (scene.get("regime") or {}).get("gamma_sign")
    magnet = ((scene.get("magnet") or {}).get("top_strikes") or [{}])[0].get("strike")
    one_sigma = (scene.get("scale") or {}).get("one_sigma_dollars")

    assert spot == 1500.0, "price.live_spot"
    assert regime == "positive", "regime.gamma_sign"
    assert magnet == 1500.0, "magnet.top_strikes[0].strike"
    assert one_sigma == 40.0, "scale.one_sigma_dollars"


def test_a_quiet_turn_says_unchanged_and_still_knows_the_spot(tape):
    g = convo._SceneGate()
    first = g.block(tape)
    assert first.startswith('<scene ts="13:01 ET">\n{'), "first turn ships the JSON"

    second = g.block(tape)
    assert "unchanged since last turn" in second
    # the tell: a gate that lost its spot read says "spot None" here, which is
    # what a rename produces and what nothing else would
    assert "spot None" not in second and "spot 1500.0" in second


def test_a_moved_spot_reopens_the_gate(tape):
    """0.15 sigma on a 40-dollar sigma is 6 dollars. If `live_spot` ever reads
    None this comparison is skipped entirely and the gate never reopens on
    price — the silent failure, caught by its consequence rather than its
    spelling."""
    import json
    g = convo._SceneGate()
    g.block(tape)

    d = Path(SR._diary_dir()) / "2026-08-19.jsonl"
    d.write_text(d.read_text()
                 + json.dumps(_row(tape + timedelta(minutes=1), 1520.0)) + "\n")
    out = g.block(tape + timedelta(minutes=1))
    assert "unchanged since last turn" not in out
    assert '"live_spot": 1520.0' in out


def test_a_moved_magnet_reopens_the_gate(tape):
    """`sig` is the other half of the gate, and `top_strikes` changed SHAPE in
    sr-7 — `[0][0]` on an object is undefined, not an error."""
    import json
    g = convo._SceneGate()
    g.block(tape)

    moved = _row(tape + timedelta(minutes=1), 1500.0)
    moved["gex_views"]["mass_by_strike"] = [[1550.0, 9.0], [1500.0, 5.0]]
    d = Path(SR._diary_dir()) / "2026-08-19.jsonl"
    d.write_text(d.read_text() + json.dumps(moved) + "\n")

    out = g.block(tape + timedelta(minutes=1))
    assert "unchanged since last turn" not in out


def test_no_tape_is_said_plainly_not_faked(tape, tmp_path):
    (Path(SR._diary_dir()) / "2026-08-19.jsonl").unlink()
    assert convo.build_voice_scene(tape) is None
    assert "the tape has not started" in convo._SceneGate().block(tape)
