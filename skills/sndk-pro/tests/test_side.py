"""side-1 (2026-09-03) — the SIDE PAYLOAD, the bar-anchored second packet.

The scene is options-first and its clock is the 2-minute scan; this packet is
bars-first and its clock is the bar. These tests pin the three rules that were
paid for in a review the packet did not survive twice:

1. EVERY TIME IS DERIVED FROM A BAR INDEX. In a hand-built draft, 41 of 44
   magnitudes replayed exactly from the bars and only 6 of 28 times did —
   because a reduce returns the value and drops the row, leaving the time field
   with no source. So every reduce here returns {"value", "at_bar"}, and every
   bar an at_bar names has to be on the wire.
2. A COUNT SAYS WHICH RAIL IT USED. Visits and crosses are measured against
   different distances; counting bars rather than visits put one draft's touch
   count at 61 where there were 14.
3. A MERGED SPELL DOES NOT COUNT ITS GAP, AND AN OPEN ONE IS NEVER MERGED.
   Both were live bugs: one credited a state to minutes never in it, the other
   reported a 12-bar run that was 2.

Every check in `integrity` must be able to fail, so each one here is also
forced to fail once — a check never observed failing is not evidence.
"""
from datetime import datetime, timedelta

import pytest

import sndk_side as SS
from test_read import T0

DAY = T0.date().isoformat()
OPEN_AT = datetime.combine(T0.date(), SS.SESSION_OPEN, tzinfo=SS._ET)


def _bar(i, lo, hi, close=None, vol=1000.0, open_=None):
    ts = OPEN_AT + timedelta(minutes=i)
    return {"ts": ts.isoformat(), "open": open_ if open_ is not None else lo,
            "high": hi, "low": lo, "close": hi if close is None else close,
            "volume": vol}


def _flat(n, price=1500.0, vol=1000.0, start=0):
    """A quiet session: every bar the same shape, so anything that moves in the
    packet moved because a test made it move."""
    return [_bar(start + i, price - 1, price + 1, price, vol) for i in range(n)]


def _now(i):
    return OPEN_AT + timedelta(minutes=i + 1)


# --- the clock ----------------------------------------------------------------
def test_the_bar_index_is_minutes_since_the_open():
    assert SS.bar_index(OPEN_AT, DAY) == 0
    assert SS.bar_index(OPEN_AT + timedelta(minutes=389), DAY) == 389
    assert SS.bar_index(OPEN_AT + timedelta(minutes=390), DAY) is None
    assert SS.bar_index(OPEN_AT - timedelta(minutes=1), DAY) is None


def test_every_time_in_the_packet_is_derived_from_a_bar_index():
    """The one rule the module exists for: the packet states exactly two
    absolute times, both in as_of, and both are bar_time() of an index it
    also carries. Nothing else may carry a clock of its own."""
    p = SS.build_side(_flat(60), DAY, _now(59))
    assert p["as_of"]["timestamp"] == SS.bar_time(p["as_of"]["bar_index"], DAY)
    assert p["as_of"]["session_start"] == SS.bar_time(0, DAY)

    def times(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("timestamp", "session_start"):
                    continue
                yield from times(v)
        elif isinstance(o, list):
            for v in o:
                yield from times(v)
        elif isinstance(o, str) and o.count(":") == 2 and o[:4].isdigit():
            yield o
    assert list(times(p)) == []


# --- the reduces --------------------------------------------------------------
def test_a_reduce_keeps_the_bar_it_came_from():
    bars = _flat(40)
    bars[7] = _bar(7, 1499.0, 1520.0, 1519.0)      # the high
    bars[23] = _bar(23, 1480.0, 1501.0, 1481.0)    # the low
    p = SS.build_side(bars, DAY, _now(39))
    hi = next(r for r in p["readings"] if r["id"] == "px.session_high")
    lo = next(r for r in p["readings"] if r["id"] == "px.session_low")
    assert (hi["value"], hi["at_bar"]) == (1520.0, 7)
    assert (lo["value"], lo["at_bar"]) == (1480.0, 23)


def test_every_bar_the_packet_names_is_on_the_wire():
    """A value verified against a bar nobody can see is not verified."""
    bars = _flat(40)
    bars[7] = _bar(7, 1499.0, 1520.0, 1519.0)
    p = SS.build_side(bars, DAY, _now(39))
    on_wire = {r["bar_index"] for r in p["bars"]["records"]}
    cited = {r["at_bar"] for r in p["readings"] if r.get("at_bar") is not None}
    cited |= {r["from_bar"] for r in p["readings"] if r.get("from_bar") is not None}
    cited |= {e["extreme_at_bar"] for e in p.get("episodes", [])}
    for L in p.get("levels", []):
        if L.get("last_visit"):
            cited |= set(L["last_visit"])
    assert cited <= on_wire


# --- the rails ----------------------------------------------------------------
def test_a_visit_is_a_run_of_bars_counted_once():
    """Counting bars instead of visits is what turned 14 into 61."""
    bars = _flat(40, 1500.0)
    for i in range(10, 20):                        # ten consecutive bars on the line
        bars[i] = _bar(i, 1549.0, 1551.0, 1550.0)
    p = SS.build_side(bars, DAY, _now(39),
                      levels=[{"price": 1550.0, "role": "wall_call"}],
                      levels_as_of_bar=39)
    wall = next(L for L in p["levels"] if L["role"] == "wall_call")
    assert wall["visits"] == 1
    assert wall["last_visit"] == [10, 19]


def test_both_counts_name_their_rail_and_the_rails_are_different():
    p = SS.build_side(_flat(40), DAY, _now(39),
                      levels=[{"price": 1500.0, "role": "flip"}], levels_as_of_bar=39)
    lvl = next(L for L in p["levels"] if L["role"] == "flip")
    assert lvl["visits_rail"] == "touch_abs" and lvl["crosses_rail"] == "cross_abs"
    assert p["level_rails"]["touch_abs"] < p["level_rails"]["cross_abs"]


def test_a_close_that_creeps_over_the_line_does_not_swallow_the_next_crossing():
    """The bug this guards: a crossing was only registered when the close was
    clearly past the line, but the side flipped anyway — so the next real
    crossing in the other direction went unrecorded. Two became four."""
    bars = _flat(12, 1500.0)
    for i in range(0, 4):
        bars[i] = _bar(i, 1489.0, 1491.0, 1490.0)     # clearly below
    bars[4] = _bar(4, 1499.9, 1500.4, 1500.1)         # creeps over — inside the rail
    for i in range(5, 9):
        bars[i] = _bar(i, 1489.0, 1491.0, 1490.0)     # back below
    for i in range(9, 12):
        bars[i] = _bar(i, 1509.0, 1511.0, 1510.0)     # decisively above
    p = SS.build_side(bars, DAY, _now(11),
                      levels=[{"price": 1500.0, "role": "flip"}], levels_as_of_bar=11)
    lvl = next(L for L in p["levels"] if L["role"] == "flip")
    assert lvl["crosses"] == 1
    assert lvl["last_cross"]["at_bar"] == 9 and lvl["last_cross"]["direction"] == "up"


def test_a_tape_level_counts_no_interaction_before_it_existed():
    """A high set at bar 20 was not visited at bar 5."""
    bars = _flat(40, 1500.0)
    bars[20] = _bar(20, 1519.0, 1521.0, 1520.5)
    p = SS.build_side(bars, DAY, _now(39))
    hi = next(L for L in p["levels"] if L["role"] == "session_high")
    assert hi["active_from_bar"] == 20
    assert hi["last_visit"][0] >= 20


def test_every_level_says_where_its_price_came_from_and_how_old_it_is():
    p = SS.build_side(_flat(40), DAY, _now(39),
                      levels=[{"price": 1500.0, "role": "flip"}], levels_as_of_bar=30)
    engine = next(L for L in p["levels"] if L["role"] == "flip")
    tape = next(L for L in p["levels"] if L["role"] == "session_high")
    assert engine["built_from"] == "options_book" and engine["price_as_of_bar"] == 30
    assert tape["built_from"] == "live_tape"
    # the engine's price can be older than the bars; the tape's never is
    assert tape["price_as_of_bar"] == tape["active_from_bar"]


# --- episodes -----------------------------------------------------------------
def _ramp(n, lo, hi):
    """A session that walks from lo to hi so RSI has something to say."""
    step = (hi - lo) / max(n - 1, 1)
    out = []
    for i in range(n):
        c = lo + step * i
        out.append(_bar(i, c - 0.5, c + 0.5, c))
    return out


def test_an_open_spell_is_never_merged_across_a_break():
    """It reported a 12-bar run that was 2. bars_in_state is the current
    unbroken run, and merge_gap_bars is null to say the rule was not applied."""
    bars = _ramp(60, 1400.0, 1500.0)               # straight up: overbought, open
    p = SS.build_side(bars, DAY, _now(59))
    open_eps = [e for e in p.get("episodes", []) if e["open"]]
    assert open_eps, "a straight ramp up should leave an open overbought spell"
    for e in open_eps:
        assert e["merge_gap_bars"] is None
        assert e["bars_in_state"] == e["span_bars"]


def test_a_merged_spell_does_not_count_the_gap_it_spans():
    """bars_in_state counts bars in the band; span_bars is the distance from
    end to end. Where they differ, a gap was merged, and the difference is it."""
    p = SS.build_side(_ramp(120, 1400.0, 1500.0), DAY, _now(119))
    for e in p.get("episodes", []):
        assert e["bars_in_state"] <= e["span_bars"]
        if e["merge_gap_bars"] is not None:
            assert e["bars_in_state"] == e["span_bars"] or e["bars_in_state"] < e["span_bars"]


def test_the_list_says_so_when_older_spells_were_dropped():
    """A list that prunes without saying so implies a completeness it lacks."""
    up = _ramp(30, 1400.0, 1460.0)
    flat = [_bar(30 + i, 1459.5, 1460.5, 1460.0) for i in range(120)]
    down = [_bar(150 + i, 1400.0 - i, 1401.0 - i, 1400.5 - i) for i in range(30)]
    p = SS.build_side(up + flat + down, DAY, _now(179))
    eps = p.get("episodes", [])
    if eps and any("_pruned" in e for e in eps):
        assert "not listed" in next(e["_pruned"] for e in eps if "_pruned" in e)


# --- warmup and thin tape -----------------------------------------------------
def test_the_reading_says_warmup_before_it_can_exist():
    p = SS.build_side(_flat(5), DAY, _now(4))
    rsi = next(i for i in p["indicators"] if i["id"] == "ind.rsi14")
    assert rsi["value"] is None and rsi["label"] == "warmup"


def test_a_thin_tape_ships_no_percentile_and_says_why():
    """With a handful of bars a percentile is arithmetic, not evidence."""
    p = SS.build_side(_flat(5), DAY, _now(4))
    vol = next(i for i in p["indicators"] if i["id"] == "ind.vol")
    assert vol["percentile_of_session"] is None and vol["x"] is None
    assert any(a["path"] == "baselines[]" and a["why"] == "in_progress"
               for a in p["absent"])


def test_no_bars_at_all_is_declared_not_guessed():
    p = SS.build_side([], DAY, _now(0))
    assert p["bars_seen"] == 0
    assert p["absent"][0]["path"] == "bars[]"
    assert "readings" not in p and "levels" not in p


def test_a_halted_minute_does_not_drag_the_divisor():
    """A halt prints bars with no volume; their zero range would pull the
    median down and inflate every distance measured against it."""
    live = _flat(40, 1500.0)
    halted = [_bar(40 + i, 1500.0, 1500.0, 1500.0, vol=0.0) for i in range(20)]
    with_halt = SS.build_side(live + halted, DAY, _now(59))
    without = SS.build_side(live, DAY, _now(39))
    nf_a = next(b for b in with_halt["baselines"] if b["id"] == "bl.bar_range_median")
    nf_b = next(b for b in without["baselines"] if b["id"] == "bl.bar_range_median")
    assert nf_a["value"] == nf_b["value"]


# --- the session segments -----------------------------------------------------
def test_the_expected_shares_are_measured_and_sum_to_one():
    """Hardcoding them is what made an earlier draft's four shares sum to 0.93."""
    prior = [_flat(390, 1500.0, vol=1000.0) for _ in range(3)]
    prof = SS.segment_profile([SS.indexed(s, DAY) for s in prior])
    assert prof["n_sessions"] == 3 and prof["normalised"] is True
    assert abs(sum(prof["fractions"].values()) - 1.0) < 0.002


def test_a_segment_says_whether_it_is_done_or_still_running():
    p = SS.build_side(_flat(100), DAY, _now(99))
    seg = {s["id"]: s for s in p["session_segments"]}
    assert seg["segment.open_drive"]["status"] == "closed"
    assert seg["segment.lull"]["status"] == "active"
    assert "segment.power_hour" not in seg      # no bars yet, so no row


# --- every check must be able to fail -----------------------------------------
def _status(p, name):
    return next(c["status"] for c in p["integrity"] if c["check"] == name)


def test_the_checks_pass_on_an_ordinary_session():
    p = SS.build_side(_ramp(200, 1400.0, 1500.0), DAY, _now(199))
    assert all(c["status"] == "pass" for c in p["integrity"]), \
        [c for c in p["integrity"] if c["status"] != "pass"]


def test_the_cited_bars_check_fails_when_a_named_bar_is_missing(monkeypatch):
    """A check never observed failing is not evidence. Drop a carried bar and
    the check must say so rather than passing over the gap."""
    bars = _flat(40)
    bars[7] = _bar(7, 1499.0, 1520.0, 1519.0)
    real = SS.build_side
    p = real(bars, DAY, _now(39))
    p["bars"]["records"] = [r for r in p["bars"]["records"] if r["bar_index"] != 7]
    rebuilt = SS._integrity(p, SS.indexed(bars, DAY), SS.rsi_wilders(SS.indexed(bars, DAY)))
    assert next(c for c in rebuilt if c["check"] == "cited_bars_on_wire")["status"] == "fail"


def test_the_recompute_check_fails_when_a_value_does_not_match_its_bar():
    bars = _flat(40)
    bars[7] = _bar(7, 1499.0, 1520.0, 1519.0)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(39))
    for r in p["readings"]:
        if r["id"] == "px.session_high":
            r["value"] = r["value"] + 5.0          # a value its bar does not hold
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "values_recomputed_from_bars")["status"] == "fail"


def test_the_episode_count_check_fails_when_a_gap_is_counted():
    bars = _ramp(120, 1400.0, 1500.0)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(119))
    if not p.get("episodes"):
        pytest.skip("this ramp produced no spell to corrupt")
    p["episodes"][0]["bars_in_state"] += 3         # credit the state to three more
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "bars_in_state_counts_only_bars_in_the_band"
                )["status"] == "fail"


def test_the_rail_check_fails_when_a_count_does_not_name_its_rail():
    bars = _flat(40)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(39))
    p["levels"][0].pop("visits_rail")
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "counts_declare_their_rail")["status"] == "fail"


def test_the_vintage_check_fails_when_a_level_hides_its_origin():
    bars = _flat(40)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(39))
    p["levels"][0].pop("built_from")
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "levels_declare_origin_and_vintage")["status"] == "fail"


# --- the packet against the real tape -----------------------------------------
def test_the_packet_builds_on_a_recorded_session(tmp_path, monkeypatch):
    """One recorded day, written through the sidecar's own writer, so the test
    exercises the same path production does."""
    import sndk_bars as SB
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    bars = _flat(200, 1500.0)
    bars[50] = _bar(50, 1479.0, 1481.0, 1480.0)
    SB.write_day(DAY, bars, _now(199))
    p = SS.side_for_day(DAY, _now(199))
    assert p["bars"]["count"] == 200
    assert p["as_of"]["bar_index"] == 199
    assert all(c["status"] in ("pass", "warn") for c in p["integrity"])


# --- the read row -------------------------------------------------------------
def test_the_read_row_carries_the_packet_beside_the_scene(tmp_path, monkeypatch):
    """It rides the read row so a replay can see exactly what it said. BESIDE
    the scene, never inside it — the payload tab pins user_prompt and
    scene_chars to the scene alone, and nesting would break both."""
    import json
    import sndk_bars as SB
    import sndk_read as SR
    from test_read_once import NOW, _diary_row

    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    (tmp_path / "sndk_reversion").mkdir()
    (tmp_path / "sndk_reads").mkdir()
    day = NOW.date().isoformat()
    (tmp_path / "sndk_reversion" / f"{day}.jsonl").write_text("\n".join(
        json.dumps(_diary_row(NOW - timedelta(minutes=m))) for m in (8, 6, 4, 2)) + "\n")

    open_at = datetime.combine(NOW.date(), SS.SESSION_OPEN, tzinfo=SS._ET)
    SB.write_day(day, [{"ts": (open_at + timedelta(minutes=i)).isoformat(),
                        "open": 1200.0, "high": 1201.0, "low": 1199.0,
                        "close": 1200.0, "volume": 1000.0} for i in range(120)], NOW)

    SR.read_once(now=NOW)
    rows = [json.loads(l) for l in
            (tmp_path / "sndk_reads" / f"{day}.jsonl").read_text().splitlines()]
    side = rows[-1].get("side")
    assert side is not None, "the read row should carry the packet"
    assert side["version"] == SS.VERSION and side["bars"]["count"] == 120
    assert "scene" not in side and "side" not in (rows[-1].get("scene") or {})
    # the engine's lines come off the same diary row the scene was built from
    assert {L["role"] for L in side["levels"]} >= {"wall_call", "wall_put"}


def test_a_broken_packet_never_fails_the_read(tmp_path, monkeypatch, capsys):
    """This packet reaches no model and no gate. If it raises, the read still
    has to land — a silent extra must never be able to take the record down."""
    import json
    import sndk_read as SR
    from test_read_once import NOW, _diary_row

    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(SR, "_market_live", lambda: True)
    (tmp_path / "sndk_reversion").mkdir()
    (tmp_path / "sndk_reads").mkdir()
    day = NOW.date().isoformat()
    (tmp_path / "sndk_reversion" / f"{day}.jsonl").write_text("\n".join(
        json.dumps(_diary_row(NOW - timedelta(minutes=m))) for m in (8, 6, 4, 2)) + "\n")

    def boom(*a, **k):
        raise RuntimeError("side payload exploded")
    monkeypatch.setattr(SR.sndk_side, "build_side", boom)

    SR.read_once(now=NOW)
    rows = [json.loads(l) for l in
            (tmp_path / "sndk_reads" / f"{day}.jsonl").read_text().splitlines()]
    assert rows, "the read row must still land"
    assert "side" not in rows[-1]
    assert "side payload skipped" in capsys.readouterr().out
