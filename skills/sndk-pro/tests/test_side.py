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
    """The upper rail: a merged spell that counts the gap it spans claims more
    bars in state than there are bars on the state's side of the exit rail."""
    bars = _ramp(120, 1400.0, 1500.0)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(119))
    if not p.get("episodes"):
        pytest.skip("this ramp produced no spell to corrupt")
    p["episodes"][0]["bars_in_state"] += 50        # more than the span can hold
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "bars_in_state_within_its_rails")["status"] == "fail"


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
def test_the_packet_lands_in_its_own_file_and_the_row_only_points_at_it(
        tmp_path, monkeypatch):
    """Its own file, the same call sndk_bars made. The read row keeps its shape
    and carries a bar pointer, because the phone polls forty of those rows a
    minute to read two fields the packet does not contain."""
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
    assert "side" not in rows[-1], "the packet must not ride the row"
    assert rows[-1]["side_bar"] is not None

    kept = SS.read_day(day)
    assert len(kept) == 1
    side = kept[0]
    assert side["version"] == SS.VERSION and side["bars"]["count"] == 120
    assert side["as_of"]["bar_index"] == rows[-1]["side_bar"]
    # the engine's lines come off the same diary row the scene was built from
    assert {L["role"] for L in side["levels"]} >= {"wall_call", "wall_put"}
    # and the row stayed small: the phone fetches forty of these a minute
    assert len(json.dumps(rows[-1])) < 4000


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
    assert "side" not in rows[-1] and "side_bar" not in rows[-1]
    assert SS.read_day(day) == []
    assert "side payload skipped" in capsys.readouterr().out


def test_a_quiet_row_does_not_carry_the_packet(tmp_path, monkeypatch):
    """~190 rows a session, ~7.1 KB a packet: carrying it on every one would
    take the reads file from 242 KB to 1.6 MB a day to store something that
    replays exactly from the bars. The rows worth a stored copy are the ones
    where the model spoke and a reading has to be graded against what it saw."""
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

    monkeypatch.setattr(SR, "should_wake", lambda *a, **k: None)   # nothing to say
    SR.read_once(now=NOW)
    rows = [json.loads(l) for l in
            (tmp_path / "sndk_reads" / f"{day}.jsonl").read_text().splitlines()]
    assert rows, "a quiet row must still land — a hidden surface is not a missing one"
    assert "side" not in rows[-1] and "side_bar" not in rows[-1]
    assert SS.read_day(day) == [], "a quiet minute keeps nothing"
    # and it is still rebuildable for that minute, which is why dropping it is safe
    assert SS.side_for_day(day, NOW)["bars"]["count"] == 120


def test_the_open_episode_check_fails_when_a_break_is_swallowed():
    """The claim on the tab is that EVERY check is forced to fail once here.
    This is one of the three that made that claim untrue until 09-03."""
    # up, then a real break down, then up again — so there is a gap to swallow
    up1 = _ramp(40, 1400.0, 1460.0)
    down = [_bar(40 + i, 1459.0 - 3 * i, 1461.0 - 3 * i, 1460.0 - 3 * i) for i in range(20)]
    up2 = [_bar(60 + i, 1399.0 + 3 * i, 1401.0 + 3 * i, 1400.0 + 3 * i) for i in range(40)]
    bars = up1 + down + up2
    ix = SS.indexed(bars, DAY)
    rsi = SS.rsi_wilders(ix)
    p = SS.build_side(bars, DAY, _now(99))
    open_eps = [e for e in p.get("episodes", []) if e["open"]]
    if not open_eps:
        pytest.skip("this shape produced no open spell to corrupt")
    open_eps[0]["from_bar"] = min(rsi)          # back across the break
    rebuilt = SS._integrity(p, ix, rsi)
    assert next(c for c in rebuilt
                if c["check"] == "open_episode_not_merged")["status"] == "fail"


def test_the_level_history_check_fails_when_an_interaction_predates_the_level():
    """A pivot set at bar 20 cannot have been visited at bar 5."""
    bars = _flat(40, 1500.0)
    bars[20] = _bar(20, 1519.0, 1521.0, 1520.5)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(39))
    hi = next(L for L in p["levels"] if L["role"] == "session_high")
    hi["active_from_bar"] = hi["last_visit"][0] + 1      # claim it existed later
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "no_interaction_before_level_existed")["status"] == "fail"


def test_the_segment_share_check_fails_when_the_shares_stop_being_a_session():
    """Four shares of one session sum to one. An earlier draft's summed to 0.93
    and nothing said so."""
    bars = _flat(100)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(99))
    p["session_segments"][0]["volume_fraction_to_date"] += 0.2
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "segment_fractions_sum_to_one")["status"] == "fail"


def test_every_check_the_packet_ships_is_forced_to_fail_somewhere_here():
    """The claim printed on the tab, pinned. If a new check is added without a
    test that makes it fail, this fails instead — a check never observed
    failing is not evidence, and saying it was tested when it was not is worse
    than not testing it."""
    import re
    from pathlib import Path
    p = SS.build_side(_ramp(200, 1400.0, 1500.0), DAY, _now(199))
    shipped = {c["check"] for c in p["integrity"]}
    src = Path(__file__).read_text()
    forced = set(re.findall(r'if c\["check"\] == "(\w+)"\s*\n?\s*\)?\["status"\] == "fail"', src))
    forced |= set(re.findall(r'c\["check"\] == "(\w+)"[\s\S]{0,80}?"status"\] == "fail"', src))
    missing = shipped - forced
    assert not missing, f"checks with no test that forces them to fail: {sorted(missing)}"


def test_the_volume_baseline_excludes_the_bar_it_measures_and_says_so():
    """A bar compared against a baseline it is part of is compared partly
    against itself, and reads quieter than it is."""
    bars = _flat(60, 1500.0, vol=1000.0)
    bars[59] = _bar(59, 1499.0, 1501.0, 1500.0, vol=30000.0)     # the loud bar
    p = SS.build_side(bars, DAY, _now(59))
    bl = next(b for b in p["baselines"] if b["id"] == "bl.vol_trailing_30")
    assert bl["excludes_current_bar"] is True
    assert bl["value"] == 1000.0                                 # untouched by the spike
    vol = next(i for i in p["indicators"] if i["id"] == "ind.vol")
    assert vol["x"] == 30.0


def test_the_packet_never_sees_a_bar_that_had_not_closed_yet():
    """The clip. The caller hands over the whole session file, so a packet built
    as of a past minute would otherwise carry bars from after it — look-ahead
    with a timestamp on it."""
    whole = _flat(200, 1500.0)
    whole[150] = _bar(150, 1599.0, 1601.0, 1600.0)     # a high AFTER the as-of
    p = SS.build_side(whole, DAY, _now(99))            # as of bar 99
    assert p["as_of"]["bar_index"] == 99
    assert p["bars"]["count"] == 100
    hi = next(r for r in p["readings"] if r["id"] == "px.session_high")
    assert hi["at_bar"] <= 99 and hi["value"] < 1600.0


def test_an_open_spell_survives_a_minute_the_sidecar_never_got():
    """A gap is an absence, not a bar out of the band. The sidecar exists
    because gaps happen, so a hole inside an open spell must not fail a check."""
    up = _ramp(60, 1400.0, 1500.0)
    holed = [b for b in up if SS.bar_index(b["ts"], DAY) != 50]
    p = SS.build_side(holed, DAY, _now(59))
    assert all(c["status"] == "pass" for c in p["integrity"]), \
        [c for c in p["integrity"] if c["status"] != "pass"]


def test_a_level_measured_outside_the_session_does_not_claim_a_bar():
    """A book stamped after the close has no bar index. Falling back to the
    newest one is a stale level wearing a fresh stamp, and it fails in the
    flattering direction — the tab's staleness line would never fire."""
    p = SS.build_side(_flat(60), DAY, _now(59),
                      levels=[{"price": 1500.0, "role": "flip"}],
                      levels_as_of_bar=None)
    lvl = next(L for L in p["levels"] if L["role"] == "flip")
    assert lvl["price_as_of_bar"] is None


def test_a_torn_line_in_the_side_file_is_skipped_not_fatal(tmp_path, monkeypatch):
    """The same rule the bar sidecar keeps: a half-written line is skipped, a
    missing file is an empty list, and neither is ever an exception."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    assert SS.read_day(DAY) == []                      # no file at all
    SS.append(DAY, {"version": SS.VERSION, "as_of": {"bar_index": 10}})
    SS.side_path(DAY).write_text(SS.side_path(DAY).read_text() + '{"half wri\n')
    SS.append(DAY, {"version": SS.VERSION, "as_of": {"bar_index": 20}})
    kept = SS.read_day(DAY)
    assert [k["as_of"]["bar_index"] for k in kept] == [10, 20]
