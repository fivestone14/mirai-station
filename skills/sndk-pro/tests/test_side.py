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
from synth import T0

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


def test_the_open_carries_the_bar_it_was_read_from():
    """A FIRST is a reduce like any other. On a clean session that bar is 0; when
    the watcher missed the bell it is the first minute seen, and the bar is the
    only thing that tells the two apart."""
    bars = _flat(40)
    # open, low, high and close all differ, so only the bar's own open can pass
    bars[0] = _bar(0, 1586.0, 1607.0, 1594.9, open_=1591.0)
    p = SS.build_side(bars, DAY, _now(39))
    op = next(r for r in p["readings"] if r["id"] == "px.session_open")
    assert (op["value"], op["at_bar"]) == (1591.0, 0)


def test_an_open_read_after_a_late_start_names_the_bar_it_really_had():
    """46 minutes of a session went missing on 09-01 to a lapsed broker login.
    The packet must not call bar 27's open the session open."""
    bars = _flat(60)[27:]
    p = SS.build_side(bars, DAY, _now(59))
    op = next(r for r in p["readings"] if r["id"] == "px.session_open")
    assert op["at_bar"] == 27


def test_every_bar_the_packet_names_is_on_the_wire():
    """A value verified against a bar nobody can see is not verified. Every
    `at_bar` or `*_at_bar` anywhere in the packet is found by walking it, so a
    field added later is held to the rule without this test knowing its name;
    a change window's start, a visit's ends and a level's vintage are cited
    bars too (the vintage is the hole the 09-04 live run found).

    A kind of citation is only tested where its bar is named by nothing else:
    if the crossing bar is also the high, the packet carries it either way and
    forgetting the crossing still passes. No one packet can do that for every
    kind (the day's RSI peak IS its spell's extreme unless that spell was
    pruned), so there are three shapes, each pinned to the bars that make it bite."""
    def cited_bars(p):
        cited = set()

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if (k == "at_bar" or k.endswith("_at_bar")) and v is not None:
                        cited.add(v)
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk({k: v for k, v in p.items() if k != "bars"})
        cited |= {r["from_bar"] for r in p["readings"] if r.get("from_bar") is not None}
        for L in p.get("levels", []):
            if L["price_as_of_bar"] is not None:
                cited.add(L["price_as_of_bar"])
            cited |= set(L["last_visit"] or [])
        return cited

    # 1. under a flip line with one visit from below, then a climb over it: the
    #    vintage (12), the visit (7), the crossing (20), the change windows'
    #    starts and the day's RSI low (14) are each a bar nothing else names
    one = [_bar(i, 1489.0, 1491.0, 1490.0) for i in range(20)]
    one += [_bar(20 + j, 1509.0 + j, 1511.0 + j, 1510.0 + j) for j in range(20)]
    one[7] = _bar(7, 1489.0, 1499.0, 1498.0)
    p1 = SS.build_side(one, DAY, _now(39),
                       levels=[{"price": 1500.0, "role": "flip"}], levels_as_of_bar=12)
    flip = next(L for L in p1["levels"] if L["role"] == "flip")
    assert (flip["price_as_of_bar"], flip["last_visit"], flip["last_cross"]["at_bar"]) == (12, [7, 7], 20)
    assert next(r for r in p1["readings"] if r["id"] == "px.session_high")["at_bar"] == 39
    assert next(i for i in p1["indicators"] if i["id"] == "ind.rsi14")["day_min_at_bar"] == 14

    # 2. a second overbought spell that peaks on a jump and then saws: its
    #    extreme (44) is not the day's RSI high, which is the first spell's
    p2 = SS.build_side(_dip_then([5.0, 5.0, 15.0] + [2.0, -1.0] * 4), DAY, _now(51))
    assert [e["extreme_at_bar"] for e in p2["episodes"]] == [14, 44]

    # 3. the day's RSI high (14) sits in a spell pruned from the list, and the
    #    open (bar 0) is not the low (60): only day_max_at_bar and the open name them
    p3 = SS.build_side(_up_down_up(climb_bars=70), DAY, _now(129))
    assert next(i for i in p3["indicators"] if i["id"] == "ind.rsi14")["day_max_at_bar"] == 14
    assert [e["from_bar"] for e in p3["episodes"]] == [74]
    assert next(r for r in p3["readings"] if r["id"] == "px.session_low")["at_bar"] == 60

    for p in (p1, p2, p3):
        on_wire = {r["bar_index"] for r in p["bars"]["records"]}
        cited = cited_bars(p)
        assert cited <= on_wire, sorted(cited - on_wire)


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
    """A high set at bar 20 was not visited at bar 5 — even though bar 5 came
    within touching distance of the price bar 20 would later set."""
    bars = _flat(40, 1500.0)
    bars[5] = _bar(5, 1499.0, 1520.5, 1500.0)      # inside the touch rail of 1521
    bars[20] = _bar(20, 1519.0, 1521.0, 1520.5)
    p = SS.build_side(bars, DAY, _now(39))
    hi = next(L for L in p["levels"] if L["role"] == "session_high")
    assert hi["price"] == 1521.0 and hi["active_from_bar"] == 20
    assert hi["visits"] == 1 and hi["last_visit"] == [20, 20]


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


def _dip_then(tail):
    """Forty bars up (RSI overbought from bar 14), one 15-point drop that
    releases the band at bar 40, then a close-to-close step for each entry of
    `tail`. With +5 steps RSI is back over 70 by bar 42, so the break is two
    bars — short enough for MERGE_GAP_BARS to merge a closed spell."""
    closes = [1400.0 + 2 * i for i in range(40)]
    closes.append(closes[-1] - 15)
    for step in tail:
        closes.append(closes[-1] + step)
    return [_bar(i, c - 0.5, c + 0.5, c) for i, c in enumerate(closes)]


def _up_down_up(climb_bars=40):
    """Up, a real break down, then up again: a closed overbought spell ending
    bar 42, a closed oversold one ending bar 63, and an open overbought spell
    from bar 74 that lasts as long as the climb."""
    up1 = _ramp(40, 1400.0, 1460.0)
    down = [_bar(40 + i, 1459.0 - 3 * i, 1461.0 - 3 * i, 1460.0 - 3 * i) for i in range(20)]
    up2 = [_bar(60 + i, 1399.0 + 3 * i, 1401.0 + 3 * i, 1400.0 + 3 * i)
           for i in range(climb_bars)]
    return up1 + down + up2


def test_an_open_spell_is_never_merged_across_a_break():
    """It reported a 12-bar run that was 2. The break here is short enough to
    merge two CLOSED spells, so only the open-spell rule keeps these apart:
    bars_in_state is the current unbroken run, and merge_gap_bars is null to
    say the rule was not applied."""
    bars = _dip_then([5.0] * 20)                    # back in the band, and still in it at the end
    p = SS.build_side(bars, DAY, _now(len(bars) - 1))
    ob = [e for e in p["episodes"] if e["state"] == "overbought"]
    assert [(e["from_bar"], e["to_bar"], e["open"]) for e in ob] == [(14, 39, False), (42, None, True)]
    now_ep = ob[-1]
    assert now_ep["merge_gap_bars"] is None
    assert now_ep["bars_in_state"] == now_ep["span_bars"] == len(bars) - 42


def test_a_merged_spell_does_not_count_the_gap_it_spans():
    """bars_in_state counts bars in the band; span_bars is the distance from
    end to end. Where they differ, a gap was merged, and the difference is it:
    here bars 40 and 41, out of the band between two runs that both closed."""
    bars = _dip_then([5.0] * 8 + [-3.0] * 40)       # back in the band, then a real exit
    p = SS.build_side(bars, DAY, _now(len(bars) - 1))
    merged = next(e for e in p["episodes"] if e["merge_gap_bars"] is not None)
    assert (merged["from_bar"], merged["to_bar"], merged["open"]) == (14, 51, False)
    assert merged["span_bars"] == 38
    assert merged["bars_in_state"] == merged["span_bars"] - 2 == 36


def test_the_list_says_so_when_older_spells_were_dropped():
    """A list that prunes without saying so implies a completeness it lacks.
    Climbing to bar 129 leaves both closed spells more than 60 bars behind."""
    p = SS.build_side(_up_down_up(climb_bars=70), DAY, _now(129))
    eps = p["episodes"]
    assert [(e["state"], e["open"]) for e in eps] == [("overbought", True)]
    notes = [e["_pruned"] for e in eps if "_pruned" in e]
    assert len(notes) == 1
    assert notes[0].startswith("2 earlier spell") and "not listed" in notes[0]


# --- warmup and thin tape -----------------------------------------------------
def test_the_reading_says_warmup_before_it_can_exist():
    """Undefined until RSI_LEN + 1 bars have closed: both sides of the boundary."""
    for n, warm in ((5, True), (SS.RSI_LEN, True), (SS.RSI_LEN + 1, False)):
        p = SS.build_side(_flat(n), DAY, _now(n - 1))
        rsi = next(i for i in p["indicators"] if i["id"] == "ind.rsi14")
        if warm:
            assert rsi["value"] is None and rsi["label"] == "warmup", n
        else:
            assert rsi["value"] is not None and rsi["label"] != "warmup", n


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
    # more halted minutes than live ones, so counting them WOULD drag the median
    # to zero; with fewer, the median of the mix does not move and nothing is tested
    live = _flat(20, 1500.0)
    halted = [_bar(20 + i, 1500.0, 1500.0, 1500.0, vol=0.0) for i in range(40)]
    with_halt = SS.build_side(live + halted, DAY, _now(59))
    without = SS.build_side(live, DAY, _now(19))
    nf_a = next(b for b in with_halt["baselines"] if b["id"] == "bl.bar_range_median")
    nf_b = next(b for b in without["baselines"] if b["id"] == "bl.bar_range_median")
    assert nf_a["value"] == nf_b["value"] == 2.0


# --- the session segments -----------------------------------------------------
def _session(totals):
    """A full session whose four segments carry the given volume totals."""
    out = []
    for (_, a, b_), total in zip(SS.SEGMENTS, totals):
        out += [_bar(i, 1499.0, 1501.0, 1500.0, vol=total / (b_ - a + 1))
                for i in range(a, b_ + 1)]
    return out


def test_the_expected_shares_are_measured_and_sum_to_one():
    """Hardcoding them is what made an earlier draft's four shares sum to 0.93.
    Taken per segment, the medians of these three sessions are .2/.3/.1/.2 —
    0.8, not a session — so only normalising makes them .25/.375/.125/.25."""
    prior = [_session((400, 300, 100, 200)),
             _session((100, 600, 100, 200)),
             _session((200, 300, 300, 200)),
             _flat(200, 1500.0)]                    # a thin day: skipped, not counted
    prof = SS.segment_profile([SS.indexed(s, DAY) for s in prior])
    assert prof["n_sessions"] == 3 and prof["normalised"] is True
    assert prof["fractions"] == {"open_drive": 0.25, "lull": 0.375,
                                 "afternoon": 0.125, "power_hour": 0.25}
    assert abs(sum(prof["fractions"].values()) - 1.0) < 0.002
    # one usable session is not a profile
    assert SS.segment_profile([SS.indexed(prior[0], DAY)]) is None


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


@pytest.mark.parametrize("reading", ["px.session_open", "px.session_high", "px.session_low"])
def test_the_recompute_check_fails_when_a_value_does_not_match_its_bar(reading):
    """Each value the check recomputes is corrupted in turn — the low had no
    test at all, so a check that skipped it would have passed clean."""
    bars = _flat(40)
    bars[7] = _bar(7, 1499.0, 1520.0, 1519.0)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(39))
    for r in p["readings"]:
        if r["id"] == reading:
            r["value"] = r["value"] + 5.0          # a value its bar does not hold
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "values_recomputed_from_bars")["status"] == "fail"


def test_the_episode_count_check_fails_when_a_gap_is_counted():
    """The upper rail: a merged spell that counts the gap it spans claims more
    bars in state than there are bars on the state's side of the exit rail.
    The corruption is that bug exactly — the span written as the count — not an
    arbitrary overshoot, so a check loose by a bar or two cannot pass it."""
    bars = _dip_then([5.0] * 8 + [-3.0] * 40)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(len(bars) - 1))
    assert _status(p, "bars_in_state_within_its_rails") == "pass"
    merged = next(e for e in p["episodes"] if e["merge_gap_bars"] is not None)
    merged["bars_in_state"] = merged["span_bars"]  # the gap counted
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
    from synth import NOW, _diary_row

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

    # the reading model is not what this test is about; the suite-wide guard
    # in conftest makes a real call fail, so stub it here
    from synth import _v2_reply
    monkeypatch.setattr(SR, "call_the_model",
                        lambda *a, **k: (_v2_reply(), None, 1.0, None))
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
    # strikes-1 (09-05): the reading carries clusters, sides and points now; a full
    # wake row measured 4,623 bytes, so the ceiling moves to 6,500 and stays a ceiling
    assert len(json.dumps(rows[-1])) < 6500


def test_a_broken_packet_never_fails_the_read(tmp_path, monkeypatch, capsys):
    """This packet reaches no model and no gate. If it raises, the read still
    has to land — a silent extra must never be able to take the record down."""
    import json
    import sndk_read as SR
    from synth import NOW, _diary_row

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

    # the reading model is not what this test is about; the suite-wide guard
    # in conftest makes a real call fail, so stub it here
    from synth import _v2_reply
    monkeypatch.setattr(SR, "call_the_model",
                        lambda *a, **k: (_v2_reply(), None, 1.0, None))
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
    from synth import NOW, _diary_row

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
    bars = _up_down_up()
    ix = SS.indexed(bars, DAY)
    rsi = SS.rsi_wilders(ix)
    p = SS.build_side(bars, DAY, _now(99))
    assert _status(p, "open_episode_not_merged") == "pass"
    open_eps = [e for e in p["episodes"] if e["open"]]
    assert [e["from_bar"] for e in open_eps] == [74]
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


def test_a_level_may_not_be_stamped_ahead_of_the_packets_own_clock():
    """Found live on 2026-09-04: a packet stamped at bar 27 carried option-book
    levels stamped bar 29. The book is sampled continuously so it CAN be fresher
    than the last closed minute — but citing a bar that has not closed, and is
    not on the wire, breaks the one promise this packet makes."""
    p = SS.build_side(_flat(40), DAY, _now(39),
                      levels=[{"price": 1500.0, "role": "flip"}],
                      levels_as_of_bar=999)                  # the book, running ahead
    lvl = next(L for L in p["levels"] if L["built_from"] == "options_book")
    assert lvl["price_as_of_bar"] == p["as_of"]["bar_index"]
    assert lvl["price_fresher_than_bar"] is True             # clamped, and says so
    assert _status(p, "no_vintage_ahead_of_as_of") == "pass"


def test_the_vintage_check_fails_when_a_level_cites_an_unclosed_bar():
    bars = _flat(40)
    ix = SS.indexed(bars, DAY)
    p = SS.build_side(bars, DAY, _now(39),
                      levels=[{"price": 1500.0, "role": "flip"}], levels_as_of_bar=30)
    lvl = next(L for L in p["levels"] if L["built_from"] == "options_book")
    lvl["price_as_of_bar"] = p["as_of"]["bar_index"] + 2      # two bars into the future
    rebuilt = SS._integrity(p, ix, SS.rsi_wilders(ix))
    assert next(c for c in rebuilt
                if c["check"] == "no_vintage_ahead_of_as_of")["status"] == "fail"
