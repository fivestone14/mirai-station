import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gex_polarity_ab as pab

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 1, 10, 0, tzinfo=ET)


def _path(seq):
    # seq: (minutes_after_t0, high, low)
    return [(T0 + timedelta(minutes=m), hi, lo) for (m, hi, lo) in seq]


def test_pin_toward_magnet_above():
    # spot 7490, magnet 7500, sigma 10 -> pin_lvl 7492.5, brk_lvl 7486.5
    assert pab.grade_snapshot(7490, 7500, 10, T0, _path([(1, 7491, 7489), (2, 7493, 7491)])) == "pin"


def test_break_away_from_magnet_above():
    assert pab.grade_snapshot(7490, 7500, 10, T0, _path([(1, 7491, 7486)])) == "break"


def test_pin_toward_magnet_below():
    # magnet below: pin when low <= 7487.5, break when high >= 7493.5
    assert pab.grade_snapshot(7490, 7480, 10, T0, _path([(1, 7491, 7487)])) == "pin"


def test_undecided_when_magnet_too_close():
    assert pab.grade_snapshot(7490, 7491, 10, T0, _path([(1, 7495, 7485)])) == "undecided"


def test_undecided_when_no_line_touched():
    assert pab.grade_snapshot(7490, 7500, 10, T0, _path([(1, 7491, 7489)])) == "undecided"


def test_grade_day_tally_and_verdict():
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490, "sigma": 10,
             "gex_theta": {"regime": "long_gamma", "magnet": 7500}}]        # predicts pin
    bars = [{"ts": (T0 + timedelta(minutes=2)).isoformat(), "high": 7493, "low": 7491}]  # -> pin
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    assert res["overall"]["n"] == 1
    assert res["overall"]["current_hit_rate"] == 1.0        # predicted pin, got pin
    assert res["overall"]["inverted_hit_rate"] == 0.0


def test_grade_day_grades_both_engines():
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490, "sigma": 10,
             "gex_views": {"regime": "long_gamma", "magnet": 7500},   # predicts pin
             "gex_theta": {"regime": "short_gamma", "magnet": 7500}}]  # predicts break
    bars = [{"ts": (T0 + timedelta(minutes=2)).isoformat(), "high": 7493, "low": 7491}]  # -> pin
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    assert res["engines"]["gex_views"]["overall"]["current_hit_rate"] == 1.0
    assert res["engines"]["gex_theta"]["overall"]["current_hit_rate"] == 0.0
    assert res["overall"]["current_hit_rate"] == 0.0   # top level mirrors gex_theta


def test_magnet_gravitation_stats():
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490, "sigma": 10,
             "gex_views": {"regime": "long_gamma", "magnet": 7500}}]
    # closest bar range to 7500 is 7491–7493 -> 7 pts away = 0.7σ, never tagged
    bars = [{"ts": (T0 + timedelta(minutes=2)).isoformat(), "high": 7493, "low": 7491}]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    m = res["engines"]["gex_views"]["tickers"]["SPX"]["magnet"]
    assert m["n"] == 1 and m["tag_rate"] == 0.0
    assert abs(m["median_approach_sigma"] - 0.7) < 1e-9


def test_magnet_tagged_when_bar_contains_it():
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490, "sigma": 10,
             "gex_views": {"regime": "long_gamma", "magnet": 7500}}]
    bars = [{"ts": (T0 + timedelta(minutes=2)).isoformat(), "high": 7501, "low": 7495}]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    m = res["engines"]["gex_views"]["tickers"]["SPX"]["magnet"]
    assert m["tag_rate"] == 1.0 and m["median_approach_sigma"] == 0.0


def _row(minutes, regime, magnet, ticker="SPX"):
    return {"ticker": ticker, "ts": (T0 + timedelta(minutes=minutes)).isoformat(),
            "spot": 7490, "sigma": 10,
            "gex_theta": {"regime": regime, "magnet": magnet}}


def test_episodes_collapse_repeated_calls():
    rows = [_row(0, "long_gamma", 7500), _row(5, "long_gamma", 7500),
            _row(10, "long_gamma", 7500.3)]           # drift < 0.1σ → same idea
    assert len(pab._episodes(rows, "gex_theta")) == 1
    bars = [{"ts": (T0 + timedelta(minutes=2)).isoformat(), "high": 7493, "low": 7491}]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    s = res["engines"]["gex_theta"]["tickers"]["SPX"]
    assert s["n"] == 1 and s["scans"] == 3            # one idea, three restatements


def test_new_episode_on_direction_or_magnet_change():
    rows = [_row(0, "long_gamma", 7500),
            _row(5, "long_gamma", 7530),              # magnet jumped 3σ → new idea
            _row(10, "short_gamma", 7530)]            # direction flipped → new idea
    assert len(pab._episodes(rows, "gex_theta")) == 3


def test_gap_breaks_an_episode():
    rows = [_row(0, "long_gamma", 7500),
            _row(5, "unknown", None),                 # unreadable scan splits the run
            _row(10, "long_gamma", 7500)]
    assert len(pab._episodes(rows, "gex_theta")) == 2


def test_meta_distinguishes_no_rows_from_no_bars():
    res = pab.grade_day("2026-07-01", rows=[], bars_lookup=lambda t: [])
    assert res["meta"]["rows"] == 0 and res["engines"] == {}
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490, "sigma": 10,
             "gex_theta": {"regime": "long_gamma", "magnet": 7500}}]
    res = pab.grade_day("2026-07-01", rows=rows, bars_lookup=lambda t: [])
    assert res["meta"]["rows"] == 1 and res["meta"]["bars"] == {"SPX": 0}
    assert res["engines"] == {}          # rows but no bars → nothing gradeable


def test_persist_writes_report_and_upserts_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "SKILL_DIR", tmp_path / "skills" / "mirai-left-eye")
    res = {"day": "2026-07-01", "tickers": {}, "overall": {}, "engines": {}}
    out = pab.persist(res)
    assert out.exists() and out.name == "polarity-2026-07-01.json"
    pab.persist(res)   # second run same day → history still has ONE line for it
    hist = tmp_path / "state" / "reversion" / "polarity_history.jsonl"
    lines = [ln for ln in hist.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1


def _lens_row(minutes, direction="call", fired=True):
    return {"ticker": "SPX", "ts": (T0 + timedelta(minutes=minutes)).isoformat(),
            "spot": 7490, "sigma": 10,
            "reversion_extreme": {"fired": fired, "direction": direction}}


def test_lens_report_card_collapses_clustered_fires():
    # THE CLUSTERING GUARD: a fire held across 3 consecutive scans = ONE verdict
    rows = [_lens_row(0), _lens_row(5), _lens_row(10)]
    bars = [{"ts": (T0 + timedelta(minutes=m)).isoformat(),
             "high": 7493 if m > 2 else 7491, "low": 7489} for m in range(1, 40)]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    lens = res["lens"]
    assert lens["n"] + lens["scratch"] + lens["pending"] == 1   # one idea, one verdict
    assert lens["wins"] == 1                                     # +0.25σ hit before −0.35σ


def test_lens_verdicts_not_duplicated_on_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "SKILL_DIR", tmp_path / "skills" / "x")
    rows = [_lens_row(0)]
    bars = [{"ts": (T0 + timedelta(minutes=2)).isoformat(), "high": 7493, "low": 7491}]
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    pab.persist(res)
    pab.persist(res)   # nightly re-run: upsert, never append
    hist = tmp_path / "state" / "reversion" / "polarity_history.jsonl"
    lines = [l for l in hist.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["lens"]["n"] == 1


def test_pin_profile_stats_strength_vs_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "SKILL_DIR", tmp_path / "skills" / "x")
    hist_dir = tmp_path / "state" / "reversion"
    hist_dir.mkdir(parents=True)
    # two prior days with eod totals 100 and 200 → baseline median = 100
    prior = [{"day": f"2026-06-{d}", "engines": {"gex_views": {"tickers": {"SPX": {
              "pin_profile": {"eod_pin_total": v}}}}}} for d, v in (("29", 100.0), ("30", 200.0))]
    (hist_dir / "polarity_history.jsonl").write_text(
        "\n".join(json.dumps(p) for p in prior) + "\n")
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490, "sigma": 10,
             "gex_views": {"regime": "long_gamma", "magnet": 7500,
                            "pin_total_gamma": 160.0, "pin_top_share": 0.62}}]
    res = pab.grade_day("2026-07-01", rows=rows, bars_lookup=lambda t: [])
    prof = res["engines"]["gex_views"]["tickers"]["SPX"]["pin_profile"]
    assert prof["eod_pin_total"] == 160.0 and prof["top_share_med"] == 0.62
    # baseline = median of [100, 200] (upper-median convention) = 200 → 160/200
    assert abs(prof["strength_vs_hist"] - 0.8) < 0.01


def test_magnet_stats_excludes_spot_hugging_magnets():
    # a magnet parked on spot (< 0.25σ away at issuance) must earn nothing —
    # only calls with real distance are a test of pull
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7500, "sigma": 10,
             "gex_theta": {"regime": "long_gamma", "magnet": 7501}},   # 0.1σ → hugging
            {"ticker": "SPX", "ts": T0.isoformat(), "spot": 7500, "sigma": 10,
             "gex_theta": {"regime": "long_gamma", "magnet": 7504}}]   # 0.4σ → eligible
    path = [(T0 + timedelta(minutes=5), 7504.5, 7503.5)]               # tags the far magnet
    st = pab._magnet_stats(rows, "gex_theta", path)
    assert st["n"] == 1 and st["n_hugging"] == 1 and st["tag_rate"] == 1.0


def test_bars_persist_and_replay():
    # grade_day saves real-range bars; _default_bars replays them before any fetch
    bars = [{"ts": T0.isoformat(), "high": 7510.0, "low": 7500.0, "close": 7505.0}]
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7500, "sigma": 10,
             "gex_theta": {"regime": "long_gamma", "magnet": 7505}}]
    res = pab.grade_day("2026-07-01", rows=rows, bars_lookup=lambda tk: bars)
    assert res["meta"]["bars_kind"]["SPX"] == "ohlc"
    assert pab._default_bars("SPX", "2026-07-01") == bars      # replayed from disk
    # flat point-bars are marked as estimates and never persisted
    flat = [{"ts": T0.isoformat(), "high": 7505.0, "low": 7505.0, "close": 7505.0}]
    res2 = pab.grade_day("2026-07-02", rows=rows, bars_lookup=lambda tk: flat)
    assert res2["meta"]["bars_kind"]["SPX"] == "point"
    assert not (pab._bars_dir() / "2026-07-02-SPX.json").exists()


def _day_line(day, wins, n, kind="ohlc", key="lens"):
    return json.dumps({"day": day, "meta": {"bars_kind": {"SPX": kind}},
                       key: {"wins": wins, "n": n}})


def test_promotion_gate_demands_a_real_record(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text("")                                        # no record → too thin
    ok, why = pab.promotion_gate(hist=hist)
    assert not ok and "too thin" in why
    # HONEST STATS (07-05): one huge winning DAY is still ONE observation —
    # 26/36 trades in a single afternoon all watched the same market move.
    hist.write_text(_day_line("2026-07-01", 26, 36) + "\n")
    ok, why = pab.promotion_gate(hist=hist)
    assert not ok and "too thin" in why
    # 16 decided days, 13 winning → clears (Wilson floor > 0.50, rate ≥ 0.58)
    lines = [_day_line(f"2026-07-{i:02d}", 3, 4) for i in range(1, 14)]        # 13 hit days
    lines += [_day_line(f"2026-08-{i:02d}", 1, 4) for i in range(1, 4)]        # 3 miss days
    hist.write_text("\n".join(lines) + "\n")
    ok, why = pab.promotion_gate(hist=hist)
    assert ok and "clears the gate" in why and "days" in why
    # the SAME record graded on flat point-bars is an estimate, not evidence
    hist.write_text("\n".join(
        [_day_line(f"2026-07-{i:02d}", 3, 4, kind="point") for i in range(1, 14)]) + "\n")
    assert pab.promotion_gate(hist=hist)[0] is False


def test_promotion_gate_even_days_teach_nothing(tmp_path):
    # a 2-2 day is neither a hit nor a miss — it must not count toward the n
    hist = tmp_path / "hist.jsonl"
    hist.write_text("\n".join(
        [_day_line(f"2026-07-{i:02d}", 2, 4) for i in range(1, 20)]) + "\n")
    ok, why = pab.promotion_gate(hist=hist)
    assert not ok and "0/12" in why


def test_lens_card_carries_outcome_vector_rollup():
    rows = [{"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490.0, "sigma": 10.0,
             "reversion_extreme": {"fired": True, "direction": "call"}}]
    bars = [{"ts": (T0 + timedelta(minutes=5)).isoformat(),
             "high": 7494.0, "low": 7489.0, "close": 7493.0}]     # clean win
    res = pab.grade_day("2026-07-01", rows=rows,
                        bars_lookup=lambda t: bars if t == "SPX" else [])
    lens = res["lens"]
    assert lens["total_r"] == 1.0 and lens["median_r"] == 1.0
    assert lens["params"] == {"target": 0.30, "stop": 0.30, "max_hold_min": 120,
                              "hold_scaled": True}   # N18: barriers on the window's σ
    assert lens["grade_v"] == 2
    assert lens["trades"][0]["r"] == 1.0


# --- HEAD C: the BREAK LENS report cards (offense — level_reclaim cock→fire) ---

def _break_row(minutes, direction="put", fired=False, fired_pre_gates=False):
    """A diary row where the Break Lens spoke through the level_reclaim key:
    `fired` = the gated fire, `fired_pre_gates` = the ungated twin (both
    one-shot — True only on the trigger scan)."""
    return {"ticker": "SPX", "ts": (T0 + timedelta(minutes=minutes)).isoformat(),
            "spot": 7490.0, "sigma": 10.0,
            "level_reclaim": {"score": 2.0, "confidence": "medium",
                              "fired": fired, "fired_pre_gates": fired_pre_gates,
                              "direction": direction,
                              "break_state": "fired" if fired else "cocked"}}


# put continuation from 7490 σ10: target 7487, stop 7493
_PUT_WIN_BAR = {"high": 7491.0, "low": 7486.0, "close": 7487.0}
_PUT_LOSS_BAR = {"high": 7494.0, "low": 7489.0, "close": 7493.0}


def _break_grade(rows, bar):
    bars = [{"ts": (T0 + timedelta(minutes=5)).isoformat(), **bar}]
    return pab.grade_day("2026-07-01", rows=rows,
                         bars_lookup=lambda t: bars if t == "SPX" else [])


def test_break_lens_grades_gated_and_ungated_ledgers():
    # one gated fire (which is also a pre-gates fire) → both cards carry it
    res = _break_grade([_break_row(0, fired=True, fired_pre_gates=True)], _PUT_WIN_BAR)
    bl = res["break_lens"]
    assert bl["n"] == 1 and bl["wins"] == 1 and bl["win_rate"] == 1.0
    assert bl["pre_gates"] == {"n": 1, "wins": 1, "win_rate": 1.0}
    assert bl["trades"][0]["dir"] == "put" and bl["trades"][0]["r"] == 1.0
    blp = res["break_lens_pre_gates"]
    assert blp["n"] == 1 and blp["wins"] == 1 and blp["win_rate"] == 1.0
    assert "params" not in blp and "pre_gates" not in blp   # thin record card


def test_break_lens_ungated_only_day_still_accrues():
    # the fail-closed tape gate means zero gated fires is the NORMAL early
    # state (lob_flow absent → no fire) — the ungated twin must still grade
    res = _break_grade([_break_row(0, fired_pre_gates=True)], _PUT_LOSS_BAR)
    bl = res["break_lens"]
    assert bl["n"] == 0 and bl["win_rate"] is None            # gated: nothing
    assert bl["pre_gates"] == {"n": 1, "wins": 0, "win_rate": 0.0}
    blp = res["break_lens_pre_gates"]
    assert blp["n"] == 1 and blp["wins"] == 0 and blp["win_rate"] == 0.0


def test_break_lens_absent_when_nothing_fired():
    # cocked-but-never-triggered day (and a fade fire) → no break keys at all
    rows = [_break_row(0), _lens_row(5)]
    res = _break_grade(rows, _PUT_WIN_BAR)
    assert "break_lens" not in res and "break_lens_pre_gates" not in res
    assert res["lens"] is not None                            # fade card unaffected


def test_break_lens_params_pin_the_gate_dials():
    # symmetric 0.30/0.30 exits (a coin flip must grade 50%) + the fire-gate
    # dials pinned so a future constant change can't silently re-price history
    res = _break_grade([_break_row(0, fired=True, fired_pre_gates=True)], _PUT_WIN_BAR)
    assert res["break_lens"]["params"] == {
        "target": 0.30, "stop": 0.30, "max_hold_min": 120,
        "cock_expire_min": 45.0, "tape_min": 0.15, "runway_min_sigma": 0.35}


def test_break_lens_clustered_restatement_grades_once():
    # the resolver's dedup: the same direction restated on consecutive fired
    # scans = ONE idea, one verdict (one-shot rows shouldn't produce this,
    # but the grader must not double-count if they ever do)
    rows = [_break_row(0, fired_pre_gates=True), _break_row(5, fired_pre_gates=True)]
    res = _break_grade(rows, _PUT_WIN_BAR)
    blp = res["break_lens_pre_gates"]
    assert blp["n"] + blp["scratch"] + blp["pending"] == 1


def test_break_lens_ignores_legacy_fired_rows_without_break_state():
    # a fail-open scan leaves the LEGACY level_reclaim dict on the row (no
    # break fields, `fired` = the old 1.3× volume-pop verdict) — it must not
    # inject a false fire into EITHER break ledger
    legacy = {"ticker": "SPX", "ts": T0.isoformat(), "spot": 7490.0, "sigma": 10.0,
              "level_reclaim": {"score": 0.6, "confidence": 0.7, "fired": True,
                                "direction": "put", "level": 7490.0,
                                "level_kind": "round", "flips_regime": False}}
    res = _break_grade([legacy, _break_row(0, fired_pre_gates=True)], _PUT_WIN_BAR)
    assert res["break_lens"]["n"] == 0                # the legacy fire never grades
    assert res["break_lens_pre_gates"]["n"] == 1      # real break rows still do
    # a day with ONLY legacy rows shows no break cards at all
    res2 = _break_grade([legacy], _PUT_WIN_BAR)
    assert "break_lens" not in res2 and "break_lens_pre_gates" not in res2


def test_break_lens_counts_pre_window_dropped_fires():
    # break fires are ONE-SHOT: a 09:40 trigger sits outside the resolver's
    # 09:45-16:00 entry window and never re-enters (a stays-lit fade would) —
    # the card must SAY the ledger under-counts the gap-day open
    early = {"ticker": "SPX", "ts": T0.replace(hour=9, minute=40).isoformat(),
             "spot": 7490.0, "sigma": 10.0,
             "level_reclaim": {"fired": False, "fired_pre_gates": True,
                               "direction": "put", "break_state": "cocked"}}
    res = _break_grade([early, _break_row(0, fired_pre_gates=True)], _PUT_WIN_BAR)
    blp = res["break_lens_pre_gates"]
    assert blp["n_dropped_early"] == 1 and blp["n"] == 1
    assert res["break_lens"]["n_dropped_early"] == 0  # no gated fire was dropped
    # even a DROP-ONLY day surfaces the card (the gap must be visible)
    res2 = _break_grade([early], _PUT_WIN_BAR)
    assert res2["break_lens_pre_gates"]["n"] == 0
    assert res2["break_lens_pre_gates"]["n_dropped_early"] == 1


def test_break_lens_surfaces_cocks_by_level():
    # cap-leak audit: distinct cocks bucketed to the 5-pt grid ride the gated
    # card, so an anchor retag minting extra budgets at one zone stays visible
    def _cocked(minutes, cocked_at, level):
        return {"ticker": "SPX", "ts": (T0 + timedelta(minutes=minutes)).isoformat(),
                "spot": 7490.0, "sigma": 10.0,
                "level_reclaim": {"fired": False, "fired_pre_gates": False,
                                  "direction": "put", "break_state": "cocked",
                                  "cocked_at": cocked_at, "cock_level": level}}
    rows = [_cocked(0, "a", 7368.0), _cocked(5, "a", 7368.0),   # restated: ONE cock
            _cocked(10, "b", 7371.0), _cocked(15, "c", 7400.0),
            _break_row(20, fired_pre_gates=True)]
    res = _break_grade(rows, _PUT_WIN_BAR)
    assert res["break_lens"]["cocks_by_level"] == {"7370": 2, "7400": 1}


def test_break_lens_cards_persist_in_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "SKILL_DIR", tmp_path / "skills" / "x")
    res = _break_grade([_break_row(0, fired=True, fired_pre_gates=True)], _PUT_WIN_BAR)
    pab.persist(res)
    hist = tmp_path / "state" / "reversion" / "polarity_history.jsonl"
    rec = json.loads([l for l in hist.read_text().splitlines() if l.strip()][0])
    assert rec["break_lens"]["n"] == 1
    assert rec["break_lens_pre_gates"]["n"] == 1              # its own top-level key


def test_promotion_gate_break_lens_reads_its_own_ledgers(tmp_path):
    # the ungated twin's record must never grant the GATED ledger anything
    # (and vice versa) — fail-closed until each accrues its own decided days
    hist = tmp_path / "hist.jsonl"
    lines = [_day_line(f"2026-07-{i:02d}", 3, 4, key="break_lens_pre_gates")
             for i in range(1, 14)]                                        # 13 hit days
    lines += [_day_line(f"2026-08-{i:02d}", 1, 4, key="break_lens_pre_gates")
              for i in range(1, 4)]                                        # 3 miss days
    hist.write_text("\n".join(lines) + "\n")
    ok, why = pab.promotion_gate(hist=hist, record_key="break_lens")
    assert not ok and "too thin" in why                       # gated: no record yet
    assert pab.promotion_gate(hist=hist, record_key="break_lens_pre_gates")[0] is True
    assert pab.promotion_gate(hist=hist)[0] is False          # fade lens: untouched


# --- N4: the stance grader (the prison-yard axis, finally scored) -----------------
def test_grade_stance_fight_and_settle_scored_against_eruption():
    # judged rows: one FIGHT call before a real eruption (right), one SETTLE call
    # before the same eruption (wrong), one no_read (abstains), one with an
    # incomplete window (abstains). Band = 0.30·√(60/390)·σ ≈ 0.1177σ → 11.2 pts at σ95.
    sig = 95.0
    rows = [
        {"ts": T0.isoformat(), "spot": 7500.0, "sigma": sig,
         "watchtower": {"fired": True, "direction": "call", "stance": "fight",
                        "horizon_min": 60, "votes": {"n": 3}}},
        {"ts": T0.isoformat(), "spot": 7500.0, "sigma": sig,
         "watchtower": {"fired": False, "direction": None, "stance": "settle",
                        "horizon_min": 60, "votes": {"n": 1}}},
        {"ts": T0.isoformat(), "spot": 7500.0, "sigma": sig,
         "watchtower": {"fired": False, "direction": None, "stance": "no_read",
                        "horizon_min": 60, "votes": {"n": 1}}},
    ]
    # bars: an eruption — 30 pts up (~0.32σ) within the hour, window complete
    bars = [{"ts": (T0 + timedelta(minutes=m)).isoformat(),
             "high": 7500.0 + m, "low": 7500.0 - 2.0, "close": 7500.0 + m}
            for m in (5, 15, 30, 45, 58)]
    card = pab._grade_stance(rows, lambda tk: bars if tk == "SPX" else [])
    assert card is not None
    assert card["n"] == 2                              # no_read abstained
    assert card["fight"] == {"n": 1, "wins": 1}        # eruption → fight was right
    assert card["settle"] == {"n": 1, "wins": 0}       # settle was wrong
    assert card["acc"] == 0.5
    # containment day: nothing leaves the band → settle right, fight wrong
    calm = [{"ts": (T0 + timedelta(minutes=m)).isoformat(),
             "high": 7502.0, "low": 7498.0, "close": 7500.0}
            for m in (5, 15, 30, 45, 58)]
    card2 = pab._grade_stance(rows, lambda tk: calm if tk == "SPX" else [])
    assert card2["fight"] == {"n": 1, "wins": 0} and card2["settle"] == {"n": 1, "wins": 1}
    # no bars → None (a point-bar/blind day must not fabricate a stance record)
    assert pab._grade_stance(rows, lambda tk: []) is None
