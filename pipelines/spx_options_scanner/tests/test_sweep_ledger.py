"""SWEEP LEDGER — Head B's SINGLE scoreboard (gex_polarity_ab._grade_watchtower_sweep):
each fired call graded by the SIGNED AREA between the 1-min close path and its entry
on the called side ("like integrals in calculus"). Replaced the Trade + Scene ledgers
2026-07-17."""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gex_polarity_ab as pab

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 1, 10, 0, tzinfo=ET)


def _row(minute, direction=None, spot=7490.0, sigma=10.0, version="wt-6"):
    """A diary row; direction set = a fired issuance (the sweep universe)."""
    row = {"ticker": "SPX", "ts": (T0 + timedelta(minutes=minute)).isoformat(),
           "spot": spot, "sigma": sigma}
    if direction is not None:
        row["watchtower"] = {"fired": True, "direction": direction,
                             "magnitude_sigma": 0.5, "prompt_version": version,
                             "votes": {"n": 3}}
    return row


def _bars(*specs):
    # specs: (minutes_after_t0, close) — narrow ranges around the close; the
    # sweep is a Riemann sum on CLOSES, so hi/lo must never matter
    return [{"ts": (T0 + timedelta(minutes=m)).isoformat(),
             "high": c + 0.2, "low": c - 0.2, "close": c} for (m, c) in specs]


def _grade(rows, bars):
    return pab._grade_watchtower_sweep(rows, lambda t: bars if t == "SPX" else [])


# full session of 1-min bars (10:00 open row → 15:59 last bar), gently favorable
_FULL_UP = _bars(*[(m, 7491.0) for m in range(1, 360)])


def test_same_direction_reaffirms_fold_into_one_call():
    rows = [_row(0, "call"), _row(5, "call"), _row(10, "call")]
    sw = _grade(rows, _FULL_UP)
    assert sw["n_calls"] == 1
    c = sw["calls"][0]
    assert c["n_reaffirms"] == 2 and c["t_open"] == rows[0]["ts"]
    assert c["close_reason"] == "session_close"
    assert c["t_close"] == datetime.combine(T0.date(), datetime.min.time().replace(hour=16),
                                            tzinfo=ET).isoformat()
    assert c["verdict"] == "win" and sw["wins"] == 1


def test_dir_flip_closes_the_open_call_and_opens_its_own():
    rows = [_row(0, "call"), _row(60, "put")]
    # up 0.2σ until the flip, then down 0.4σ (favorable for the put) to the close
    bars = _bars(*[(m, 7492.0) for m in range(1, 60)],
                 *[(m, 7486.0) for m in range(61, 360)])
    sw = _grade(rows, bars)
    assert sw["n_calls"] == 2
    first, second = sw["calls"]
    assert first["close_reason"] == "dir_flip" and first["t_close"] == rows[1]["ts"]
    assert second["dir"] == "put" and second["t_open"] == rows[1]["ts"]
    assert second["close_reason"] == "session_close"
    assert first["verdict"] == "win" and second["verdict"] == "win"
    assert sw["day_hit"] == "hit"


def test_incomplete_bars_leave_the_call_pending():
    # bars stop at :60 — the session-close call can't grade yet (day running)
    sw = _grade([_row(0, "call")], _bars(*[(m, 7492.0) for m in range(1, 61)]))
    c = sw["calls"][0]
    assert c["verdict"] == "pending"
    assert c["t_close"] is None and c["close_reason"] is None
    assert sw["pending"] == 1 and sw["wins"] == 0
    # fields still computed over the available bars
    assert c["bars_win"] == 60 and c["area_sess"] == round(60 * 0.2 / 390, 5)
    # ...but a dir-flip call whose window IS covered grades immediately
    rows = [_row(0, "call"), _row(30, "put")]
    sw2 = _grade(rows, _bars(*[(m, 7492.0) for m in range(1, 61)]))
    assert sw2["calls"][0]["verdict"] == "win"          # closed at the :30 flip
    assert sw2["calls"][1]["verdict"] == "pending"      # open to 16:00, bars end :60


def test_slice_arithmetic_on_a_hand_built_path():
    # CALL from 7490 σ10, closed by a put flip at :05; slices at :01-:04:
    # closes 7491, 7487, 7496, 7492 → s = [+0.1, −0.3, +0.6, +0.2]
    rows = [_row(0, "call"), _row(5, "put")]
    bars = _bars((1, 7491.0), (2, 7487.0), (3, 7496.0), (4, 7492.0))
    sw = _grade(rows, bars)
    c = sw["calls"][0]
    assert c["area_sess"] == round(0.6 / 390, 5)        # Σ s_i / 390
    assert c["mean_fav_sigma"] == round(0.6 / 4, 4)     # Σ s_i / n_bars
    assert c["bars_win"] == 3 and c["bars_lose"] == 1
    assert c["dur_frac"] == 0.75
    assert c["mfe_sigma"] == 0.6 and c["mae_sigma"] == -0.3
    # the −0.3 slice crossed the −0.20σ stop BEFORE the 0.6 peak: the
    # tradability dial freezes at the pre-stop peak (+0.1)
    assert c["mfe_before_stop"] == 0.1
    assert c["verdict"] == "win"                        # area > 0 AND dur ≥ 0.5


def test_mfe_before_stop_equals_mfe_when_never_stopped():
    rows = [_row(0, "call"), _row(4, "put")]
    bars = _bars((1, 7491.0), (2, 7489.0), (3, 7495.0))   # s = [0.1, −0.1, 0.5]
    c = _grade(rows, bars)["calls"][0]
    assert c["mae_sigma"] == -0.1                          # never crossed −0.20σ
    assert c["mfe_before_stop"] == c["mfe_sigma"] == 0.5


def test_verdict_matrix_win_loss_mixed():
    # LOSS: every slice under the entry, dur_frac 0
    rows = [_row(0, "call"), _row(5, "put")]
    sw = _grade(rows, _bars((1, 7488.0), (2, 7488.0), (3, 7488.0)))
    assert sw["calls"][0]["verdict"] == "loss"
    assert sw["losses"] == 1 and sw["day_hit"] == "miss"
    # MIXED: area positive (one huge slice) but duration majority negative
    sw = _grade(rows, _bars((1, 7489.0), (2, 7489.0), (3, 7510.0)))
    c = sw["calls"][0]
    assert c["area_sess"] > 0 and c["dur_frac"] < 0.5
    assert c["verdict"] == "mixed"
    assert sw["mixed"] == 1 and sw["wins"] == 0 and sw["losses"] == 0
    # mixed still counts toward the day's net area
    assert sw["net_area_sess"] == c["area_sess"] and sw["day_hit"] == "hit"


def test_day_hit_is_the_sign_of_summed_area():
    # a badly losing call then a mildly winning put → net negative → MISS day
    rows = [_row(0, "call"), _row(60, "put")]
    bars = _bars(*[(m, 7480.0) for m in range(1, 60)],      # call: s = −1.0 × 59
                 *[(m, 7489.0) for m in range(61, 360)])    # put:  s = +0.1 × 299
    sw = _grade(rows, bars)
    a1, a2 = (c["area_sess"] for c in sw["calls"])
    assert a1 < 0 < a2
    assert sw["net_area_sess"] == round(a1 + a2, 5)
    assert sw["net_area_sess"] < 0 and sw["day_hit"] == "miss"


def test_out_of_window_issuances_counted_never_silently_lost():
    early = _row(0, "call")
    early["ts"] = T0.replace(hour=9, minute=40).isoformat()   # pre-window
    late = _row(0, "call")
    late["ts"] = T0.replace(hour=16, minute=5).isoformat()    # post-close
    sw = _grade([early, late, _row(0, "call")], _FULL_UP)
    assert sw["n_dropped_early"] == 2
    assert sw["n_calls"] == 1


def test_empty_day_yields_a_well_formed_zero_card():
    sw = pab._grade_watchtower_sweep([], lambda t: [])
    assert sw["sweep_grade_v"] == 1 and sw["prompt_version"] is None
    assert sw["n_calls"] == sw["wins"] == sw["losses"] == sw["mixed"] == sw["pending"] == 0
    assert sw["day_hit"] is None and sw["net_area_sess"] is None
    assert sw["med_mean_fav_sigma"] is None and sw["med_dur_frac"] is None
    assert sw["med_mfe_sigma"] is None
    assert sw["n_dropped_early"] == 0 and sw["calls"] == []
    assert sw["params"] == {"stop_sigma": 0.20,
                            "verdict": "area sign + dur_frac majority",
                            "closure": "dir_flip|session_close"}


def test_grade_day_emits_sweep_and_the_old_cards_are_gone():
    rows = [_row(0, "call"), _row(60, "put")]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: _FULL_UP if t == "SPX" else [])
    sw = res["watchtower_sweep"]
    assert sw["n_calls"] == 2 and sw["prompt_version"] == "wt-6"
    assert "watchtower" not in res                    # Trade Ledger: deleted
    assert "watchtower_scene" not in res              # Scene Ledger: deleted
    # zero-call day still carries a well-formed card (the UI contract)
    res2 = pab.grade_day("2026-07-01", rows=[], bars_lookup=lambda t: [])
    assert res2["watchtower_sweep"]["n_calls"] == 0


def test_sweep_card_summary_medians_over_closed_calls():
    rows = [_row(0, "call"), _row(5, "put"), _row(10, "call")]
    # call 1 (:00→:05): s=[+0.2]; put (:05→:10): s=[+0.3]; call 2 → session close
    bars = _bars((3, 7492.0), (8, 7487.0),
                 *[(m, 7491.0) for m in range(11, 360)])
    sw = _grade(rows, bars)
    assert sw["n_calls"] == 3 and sw["wins"] == 3
    import statistics
    closed = sw["calls"]
    assert sw["med_mean_fav_sigma"] == round(statistics.median(
        [c["mean_fav_sigma"] for c in closed]), 4)
    assert sw["med_dur_frac"] == 1.0
    assert sw["med_mfe_sigma"] == round(statistics.median(
        [c["mfe_sigma"] for c in closed]), 3)


def test_bad_sigma_rows_are_dropped_never_poisonous():
    # σ<0 would grade the call with an INVERTED sign; a non-numeric σ used to
    # raise inside the fail-open except and silently delete the whole day's card
    for bad in (-10.0, 0.0, "10", None):
        sw = _grade([_row(0, "call", sigma=bad)], _FULL_UP)
        assert sw is not None and sw["n_calls"] == 0
    # a poison row beside a clean one costs only itself
    sw = _grade([_row(0, "call", sigma=-10.0), _row(5, "call")], _FULL_UP)
    assert sw["n_calls"] == 1 and sw["calls"][0]["t_open"] == _row(5)["ts"]


def _sweep_day_line(day, day_hit, pv="wt-6", gv=1, kind="ohlc"):
    return json.dumps({"day": day, "meta": {"bars_kind": {"SPX": kind}},
                       "watchtower_sweep": {"day_hit": day_hit, "prompt_version": pv,
                                            "sweep_grade_v": gv}})


def test_promotion_gate_watchtower_reads_sweep_day_hits(tmp_path):
    # Head B's gate now counts the sweep card's day-hit bits (a clean Bernoulli:
    # sign(net area) is 50/50 under a driftless no-skill null)
    hist = tmp_path / "hist.jsonl"
    lines = [_sweep_day_line(f"2026-07-{i:02d}", "hit") for i in range(1, 14)]   # 13 hits
    lines += [_sweep_day_line(f"2026-08-{i:02d}", "miss") for i in range(1, 4)]  # 3 misses
    lines += [_sweep_day_line("2026-08-05", None)]        # undecided day: no evidence
    hist.write_text("\n".join(lines) + "\n")
    assert pab.promotion_gate(hist=hist)[0] is False      # lens: no record
    ok, why = pab.promotion_gate(hist=hist, record_key="watchtower")
    assert ok and "clears the gate" in why and "16" in why
    # a prompt bump (new era) restarts the 12-day Wilson clock
    lines += [_sweep_day_line("2026-08-10", "hit", pv="wt-7")]
    hist.write_text("\n".join(lines) + "\n")
    ok, why = pab.promotion_gate(hist=hist, record_key="watchtower")
    assert not ok and "too thin" in why


def test_promotion_gate_counts_a_duplicated_day_once_newest_winning(tmp_path):
    # persist() upserts one line per day, but a manual append can leave two rows
    # for one day — the gate must count that day ONCE, from its NEWEST row
    hist = tmp_path / "hist.jsonl"
    lines = [_sweep_day_line(f"2026-06-{i:02d}", "hit") for i in range(1, 12)]
    lines += [_sweep_day_line("2026-06-20", "miss"),      # older row for the day
              _sweep_day_line("2026-06-20", "hit")]       # newest row wins
    hist.write_text("\n".join(lines) + "\n")
    ok, why = pab.promotion_gate(hist=hist, record_key="watchtower")
    assert ok and "12 decided days" in why and "1.00" in why
    # and a newest-row-undecided day drops out instead of resurrecting the old bit
    lines[-1] = _sweep_day_line("2026-06-20", None)
    hist.write_text("\n".join(lines) + "\n")
    ok, why = pab.promotion_gate(hist=hist, record_key="watchtower")
    assert not ok and "11/12" in why


def test_promotion_gate_ignores_legacy_trade_cards(tmp_path):
    # historical polarity rows carry the RETIRED "watchtower" trade card — a
    # different exam; the sweep gate must not read a single day from them
    hist = tmp_path / "hist.jsonl"
    legacy = [json.dumps({"day": f"2026-07-{i:02d}",
                          "meta": {"bars_kind": {"SPX": "ohlc"}},
                          "watchtower": {"wins": 3, "n": 4,
                                         "prompt_version": "wt-6", "grade_v": 2}})
              for i in range(1, 20)]
    hist.write_text("\n".join(legacy) + "\n")
    ok, why = pab.promotion_gate(hist=hist, record_key="watchtower")
    assert not ok and "0/12" in why


def test_replay_day_serves_the_sweep_contract(monkeypatch):
    vs = Path(__file__).resolve().parents[3] / "runtime" / "viewstation"
    sys.path.insert(0, str(vs))
    import snapshot
    rows = [_row(0, "call"), _row(60, "put")]
    bars = _bars(*[(m, 7492.0) for m in range(1, 60)],
                 *[(m, 7486.0) for m in range(61, 360)])
    monkeypatch.setattr(pab, "_read_rows", lambda day: rows)
    monkeypatch.setattr(pab, "_default_bars", lambda tk, day: bars)
    out = snapshot.replay_day("2026-07-01")
    assert set(out) == {"day", "sweep", "fires"}          # old scene/paper keys gone
    assert out["day"] == "2026-07-01"
    assert out["sweep"]["n_calls"] == 2                   # the same on-demand card
    assert out["sweep"]["sweep_grade_v"] == 1
    fires = out["fires"]
    assert len(fires) == 2
    for f in fires:
        assert set(f) == {"ts", "dir", "outcome", "mfe_sigma", "mae_sigma",
                          "move_60m", "ttr_min", "r", "pred_magnitude_sigma"}
        assert f["pred_magnitude_sigma"] == 0.5
    # each fire's outcome is ITS parent sweep call's verdict (sweep-authoritative)
    assert fires[0]["outcome"] == out["sweep"]["calls"][0]["verdict"]
    assert fires[1]["outcome"] == out["sweep"]["calls"][1]["verdict"]
