"""strikes-1 (2026-09-05) — the STRIKES PAYLOAD, the board-first scene.

These pin the rules the design paid for:
1. Every time is derived from a bar or a book, never typed separately.
2. Absence is stated, never zero: a missing surface drops its columns and is
   named; a strike missing from the earlier book says so; a missing minute is
   counted; an empty side is named; no bars means touch columns are absent.
3. Nothing is blended: three rank columns, never one score, and a rank only
   when its surface was measured.
4. One fact, one home: the dedupe audit's second copies stay out.
5. No verdict reaches the model, including the ones that rode inside the
   since-last-read frame (the wake reason, the wall label on a crossing).
6. The output guard drops a cluster that names an unlisted strike, is not
   adjacent on the list, or repeats a rank, sets the change word from the
   rule, and keeps the model's own quiet flag.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sndk_board as B
import sndk_read as SR

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 31, 10, 0, tzinfo=ET)
OPEN_AT = datetime(2026, 7, 31, 9, 30, tzinfo=ET)

STRIKES = [1150.0, 1200.0, 1250.0, 1300.0, 1350.0, 1400.0]


def mkrow(ts, spot=1290.0, chain_spot=None, sigma=100.0, oi=None, vol=None, net=None,
          atm_iv=0.5, book_asof=None, next_arrays=False, drop_vol=False, drop_net=False):
    oi = oi or {1200.0: (240, 120), 1250.0: (180, 90), 1300.0: (540, 270), 1350.0: (120, 60),
                1400.0: (60, 30), 1150.0: (30, 15)}
    vol = vol or {k: (int(c * 0.1), int(p * 0.1)) for k, (c, p) in oi.items()}
    net = net or {1200.0: -2.0, 1250.0: 1.0, 1300.0: 5.0, 1350.0: -3.0, 1400.0: 0.5, 1150.0: -0.2}
    gv = {"magnet": 1300.0, "front_dte": 0,
          "mass_by_strike": [[k, c + p + vol[k][0] + vol[k][1]] for k, (c, p) in sorted(oi.items())],
          "net_by_strike": [[k, v] for k, v in sorted(net.items())],
          "oi_side_by_strike": [[k, c, p] for k, (c, p) in sorted(oi.items())],
          "vol_side_by_strike": [[k, c, p] for k, (c, p) in sorted(vol.items())]}
    if drop_vol:
        gv.pop("vol_side_by_strike")
    if drop_net:
        gv.pop("net_by_strike")
    if next_arrays:
        gv["next_dte"] = 7
        gv["oi_side_by_strike_next"] = [[k, c * 2, p * 2] for k, (c, p) in sorted(oi.items()) if k != 1150.0]
        gv["vol_side_by_strike_next"] = [[k, 3, 4] for k in sorted(oi) if k != 1150.0]
    return {
        "ts": ts.isoformat(), "ticker": "SNDK", "spot": spot, "sigma": sigma,
        "atm_iv": atm_iv, "prior_close": 1250.0, "gamma_sign": "positive", "regime": "neutral",
        "meta": {"chain_spot": chain_spot if chain_spot is not None else spot,
                 "book_asof": (book_asof or ts).isoformat(), "book_source": "pull",
                 "spot_source": "schwab_quote",
                 "expiries": [{"date": "2026-07-31", "dte": 0}, {"date": "2026-08-07", "dte": 7}]},
        "gex_views": gv,
    }


def mkrows(n=8, start=T0 - timedelta(minutes=14), step=2, **kw):
    return [mkrow(start + timedelta(minutes=i * step), **kw) for i in range(n)]


def bar(i, lo, hi, close=None, vol=1000.0):
    ts = OPEN_AT + timedelta(minutes=i)
    return {"ts": ts.isoformat(), "open": lo, "high": hi, "low": lo,
            "close": hi if close is None else close, "volume": vol}


def flat_bars(n, lo=1285.0, hi=1295.0):
    return [bar(i, lo, hi) for i in range(n)]


def recs(v2):
    return {r["strike"]: r for r in B.rows_as_records(v2["strikes"])}


# ---------------------------------------------------------------- the table
def test_table_has_columns_once_and_one_row_per_strike_with_no_score():
    rows = mkrows()
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    s = v2["strikes"]
    assert len(s["rows"]) >= 4
    if B.STRIKE_LAYOUT == "table":
        assert all(len(r) == len(s["columns"]) for r in s["rows"])
    else:
        assert all(set(r) <= set(s["columns"]) for r in s["rows"])   # a record never carries a field the columns do not name
    assert {"rank_by_contracts", "rank_by_volume_today", "rank_by_dealer_gamma"} <= set(s["columns"])
    assert not any("score" in c or "strength" in c for c in s["columns"])


def test_both_sides_listed_and_nearest_strikes_on_the_header():
    rows = mkrows(spot=1290.0)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    r = recs(v2)
    assert 1300.0 in r and 1250.0 in r
    assert v2["strikes"]["nearest_above"] == 1300.0 and v2["strikes"]["nearest_below"] == 1250.0
    assert "no_strikes_above" not in v2["strikes"]


def test_an_empty_side_is_stated_not_silent():
    oi = {1150.0: (30, 15), 1200.0: (240, 120), 1250.0: (180, 90)}
    net = {1150.0: -0.2, 1200.0: -2.0, 1250.0: 1.0}
    rows = mkrows(spot=1290.0, oi=oi, net=net)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert v2["strikes"]["no_strikes_above"] is True
    assert "nearest_above" not in v2["strikes"] and v2["strikes"]["nearest_below"] == 1250.0


def test_distances_divide_the_books_spot_not_the_live_one():
    rows = mkrows(spot=1290.0, chain_spot=1280.0)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert recs(v2)[1300.0]["dist_sigma"] == 0.2 and recs(v2)[1300.0]["side"] == "above"


def test_a_missing_surface_drops_its_columns_and_is_named():
    rows = mkrows(drop_vol=True)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    s = v2["strikes"]
    assert "rank_by_volume_today" not in s["columns"]
    assert any("volume by side" in a for a in s["absent"])
    rows = mkrows(drop_net=True)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    s = v2["strikes"]
    assert "rank_by_dealer_gamma" not in s["columns"] and "dealer_gamma_sign" not in s["columns"]


def test_touch_facts_come_off_the_wicks_and_their_times_off_the_bars():
    rows = mkrows(spot=1290.0)
    bars = flat_bars(20) + [bar(20, 1296.0, 1302.0, vol=5000.0), bar(21, 1297.0, 1301.0, vol=5000.0)] + [bar(i, 1285.0, 1295.0) for i in range(22, 29)]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, bars)
    r = recs(v2)[1300.0]
    assert r["touched_today"] is True and r["bars_touched_today"] == 2
    assert r["first_touch"] == (OPEN_AT + timedelta(minutes=20)).strftime("%H:%M")
    assert r["last_touch"] == (OPEN_AT + timedelta(minutes=21)).strftime("%H:%M")
    assert r["shares_traded_at_strike_pp"] == round(10000 / (27 * 1000 + 10000) * 100, 1)
    other = recs(v2)[1350.0]
    assert other["touched_today"] is False and other["first_touch"] is None


def test_no_bars_means_touch_columns_are_absent_and_the_header_says_so():
    rows = mkrows()
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, [])
    s = v2["strikes"]
    assert "touched_today" not in s["columns"] and "touched_in_books" not in s["columns"]
    assert s["touches_unavailable"] == "no_minute_bars"
    assert v2["frames"]["touches_unavailable"] == "no_minute_bars"


# ---------------------------------------------------------------- change and series
def test_change_cell_and_its_basis_on_the_header():
    rows = mkrows(n=8)
    for r in rows[4:]:
        r["gex_views"]["vol_side_by_strike"] = [[k, c + (200 if k == 1350.0 else 0), p]
                                                for k, c, p in r["gex_views"]["vol_side_by_strike"]]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), flat_bars(30))
    s = v2["strikes"]
    assert s["change_basis"] == "last_read" and s["change_books_compared"] == 4
    assert s["change_columns"] == ["contracts_share_pp", "vol_calls", "vol_puts"]
    ch = recs(v2)[1350.0]["change"]
    assert isinstance(ch, list) and ch[1] == 200


def test_first_read_falls_back_to_five_books_and_no_earlier_book_is_stated():
    rows = mkrows(n=8)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert v2["strikes"]["change_basis"] == "5_books"
    assert v2["between_frames"] == {"first_read_of_session": True}
    rows = mkrows(n=3, start=T0 - timedelta(minutes=4))     # a fresh book, too few earlier ones
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert v2["strikes"]["change_unavailable"] == "no_earlier_book"
    assert all(r["change"] is None for r in B.rows_as_records(v2["strikes"]))


def test_a_strike_missing_from_the_earlier_book_says_so():
    rows = mkrows(n=8)
    for r in rows[:4]:
        gv = r["gex_views"]
        for key in ("mass_by_strike", "net_by_strike", "oi_side_by_strike", "vol_side_by_strike"):
            gv[key] = [x for x in gv[key] if x[0] != 1400.0]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), flat_bars(30))
    assert recs(v2)[1400.0]["change"] == "strike_not_in_earlier_book"
    assert v2["strikes"]["entered_since_reference"] == [1400.0]


def test_series_aligns_to_the_frames_and_sums_only_known_entries():
    rows = mkrows(n=8, step=4)
    for i, r in enumerate(rows):
        r["gex_views"]["vol_side_by_strike"] = [[k, c + (50 * i if k == 1300.0 else 0), p]
                                                for k, c, p in r["gex_views"]["vol_side_by_strike"]]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(60))
    fr = v2["frames"]
    assert fr["books_in_series"] == 8 and len(fr["interval_min"]) == 7 and fr["interval_min"] == [4] * 7
    assert fr["book_times"][-1] == SR._book_asof(rows[-1]).astimezone(ET).strftime("%H:%M")
    r = recs(v2)[1300.0]
    assert r["vol_added_per_book"] == [50] * 7 and r["vol_added_in_series"] == 350
    assert len(fr["price_path_sigma_from_now"]) == 8 and fr["price_path_sigma_from_now"][-1] == 0.0


def test_a_long_interval_is_a_gap_and_the_series_carries_null_for_an_unseen_strike():
    rows = mkrows(n=6, step=4)
    late = mkrow(SR._ts(rows[-1]) + timedelta(minutes=30))
    rows.append(late)
    rows[2]["gex_views"]["oi_side_by_strike"] = [x for x in rows[2]["gex_views"]["oi_side_by_strike"] if x[0] != 1400.0]
    rows[2]["gex_views"]["vol_side_by_strike"] = [x for x in rows[2]["gex_views"]["vol_side_by_strike"] if x[0] != 1400.0]
    v2, _ = B.build_scene_v2(rows[-1], rows, SR._ts(late) + timedelta(seconds=30), None, None, [])
    fr = v2["frames"]
    assert fr["gaps"] and fr["gaps"][0]["minutes"] == 30
    r = recs(v2)[1400.0]
    assert r["vol_added_per_book"][1] is None and r["vol_added_per_book"][2] is None


def test_next_week_columns_ride_only_when_the_diary_kept_them():
    rows = mkrows(n=8)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert v2["strikes"]["next_book"] == "not_recorded" and "next_week" not in v2["strikes"]["columns"]
    rows = mkrows(n=8, next_arrays=True)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    s = v2["strikes"]
    assert s["next_book"] == {"days_to_expiry": 7, "expiry_date": "2026-08-07"}
    assert s["next_week_columns"] == ["oi_calls", "oi_puts", "vol_calls", "vol_puts"]
    r = recs(v2)
    assert r[1300.0]["next_week"] == [1080, 540, 3, 4]
    assert r[1150.0]["next_week"] == "strike_not_in_next_weekly_book"


# ---------------------------------------------------------------- between the frames
def test_between_frames_times_come_off_the_bars_and_carries_no_second_copy():
    rows = mkrows(n=8)
    last_read = SR._ts(rows[3])
    bars = flat_bars(30)
    bars[25] = bar(25, 1270.0, 1292.0)
    bars[28] = bar(28, 1288.0, 1310.0)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, last_read, bars)
    bf = v2["between_frames"]
    assert bf["price"]["low"] == 1270.0 and bf["price"]["low_at"] == "09:55"
    assert bf["price"]["high"] == 1310.0 and bf["price"]["high_at"] == "09:58"
    for dup in ("from", "to", "minutes", "boxes_broken", "scans", "distinct_books", "strikes_touched"):
        assert dup not in bf, dup
    assert "net_change_sigma" not in bf["price"]
    assert bf["shares_traded"]["in_gap"] == 7000   # seven completed bars from 09:53 to 09:59


def test_a_missing_minute_is_counted_never_read_as_calm():
    rows = mkrows(n=8)
    bars = [b for b in flat_bars(30) if b["ts"][11:16] not in ("09:55", "09:56")]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), bars)
    assert v2["between_frames"]["missing_minutes"] == 2


# ---------------------------------------------------------------- what leaves, what stays
VERDICT_KEYS = ("magnet", "walls", "regime", "breadth", "structure", "momentum", "dealer_positioning")


import json


def test_no_verdict_reaches_the_model_including_inside_the_frame():
    rows = mkrows(n=8)
    frame = {"last_read_at": "09:52", "minutes_since": 8, "spot_then": 1310.0, "spot_now": 1290.0,
             "spot_change_dollars": -20.0, "spot_change_sigma": -0.2, "frame_is": "a move",
             "why_this_read": "price moved to the other side of the hedging flip",
             "nothing_crossed_since_then": True,
             "unchanged_since_then": {"gamma_sign": "positive", "heaviest_strike": 1300.0, "call_wall": 1350.0},
             "walls_absent_then_and_now": ["put"],
             "crossed_since_then": [{"level": 1300.0, "direction": "up", "was_labelled_then": "nearest_call_wall"}],
             "held_between_since_last_read": {"low": 1288.0, "high": 1292.0}}
    v2, v1 = B.build_scene_v2(rows[-1], rows, T0, frame, SR._ts(rows[3]), flat_bars(30))
    # the live context's own diff block names the old sign and walls; it must not ride
    v1["context"]["changed_since_last_book"] = {"gamma_sign": {"was": "positive", "now": "negative"},
                                                "nearest_call_wall": {"was": 1350.0, "now": 1300.0}}
    v2b, _ = B.build_scene_v2(rows[-1], rows, T0, frame, SR._ts(rows[3]), flat_bars(30), v1=v1)
    assert set(v2b["context"]) <= {"ranges", "since_last_read"}
    for k in VERDICT_KEYS:
        assert k not in v2, k
    paths = B.leaf_paths(v2)
    for bad in ("frame_is", "skewed_toward", "tape_abnormal_vs_own_history", "heavier_side",
                "regime_label", "gamma_sign", "drifts_toward_strike", "share_of_book_gamma_pp",
                "walls_absent_then_and_now", "heaviest_strike", "call_wall", "put_wall",
                "top_strike_lead_pp", "lopsidedness", "why_this_read", "was_labelled_then",
                "nothing_crossed_since_then", "spot_now", "held_between_since_last_read",
                "dealer_gamma_net", "dealer_delta_musd", "passed_today"):
        # the LAST segment of a path, so dealer_gamma_sign is not mistaken for gamma_sign
        assert not any(p.rstrip("[]").split(".")[-1] == bad for p in paths), bad
    slr = v2["context"]["since_last_read"]
    assert slr["crossed_since_then"] == [{"level": 1300.0, "direction": "down"}]   # computed from price then and now
    for k in ("data_sources", "clock", "price"):
        assert k in v2
    assert v2["clock"] == v1["clock"] and v2["price"] == v1["price"]
    assert v2["scale"]["implied_vol_atm"] == 0.5


def test_data_sources_keeps_the_clocks_and_drops_the_derived_counts():
    rows = mkrows(n=8)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    ds = v2["data_sources"]
    assert "scan_taken_at" in ds and "options_book" in ds
    for gone in ("scans_so_far_today", "scan_interval_min"):
        assert gone not in ds
    for gone in ("cache_age_s", "distinct_books_so_far_today", "refresh_interval_min"):
        assert gone not in ds["options_book"]


def test_clusters_then_ride_in_the_frame():
    rows = mkrows(n=8)
    frame = {"last_read_at": "09:52", "minutes_since": 8, "spot_then": 1290.0, "spot_change_sigma": 0.0}
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, frame, SR._ts(rows[3]), flat_bars(30),
                             clusters_then=[{"center": 1300.0, "strikes": [1300.0], "rank": 1}])
    assert v2["context"]["since_last_read"]["clusters_then"] == [{"center": 1300.0, "strikes": [1300.0]}]


def test_the_gate_payload_keeps_the_old_verdicts_beside_and_takes_v1_when_given():
    rows = mkrows(n=8)
    v2, v1 = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    lg = B.legacy(rows[-1], rows, T0, v1=v1)
    assert lg["magnet"] == 1300.0 and lg["gamma_sign"] == "positive"
    assert "walls" in lg or "walls" not in v1
    assert "legacy" not in v2 and "magnet" not in v2


def test_frozen_list_agrees_with_the_table_it_was_built_from():
    rows = mkrows(n=40, start=T0 - timedelta(minutes=78))
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    top = B.rows_as_records(v2["strikes"])[0]
    fz = v2.get("frozen_do_not_cite") or []
    assert any(f.startswith(f"{top['strike']:g} top of list for") for f in fz), fz
    mins = int([f for f in fz if "top of list" in f][0].split("for ")[1].rstrip("m"))
    assert top["on_list_for_min"] >= mins >= SR.FROZEN_MIN


def test_regions_ride_on_the_legacy_scene_not_the_prompt():
    rows = mkrows(n=8)
    v2, v1 = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), flat_bars(30))
    assert "regions" not in v2                      # SHIP_REGIONS is off: the model drew 34 of 34 on the rule
    rg = v1["regions_rule"]
    listed = set(recs(v2))
    for g in rg["regions"]:
        assert set(g["strikes"]) <= listed
    assert rg["rule"] == "regions-1"
    assert "regions_rule" in B.legacy(rows[-1], rows, T0, v1=v1)


def test_a_book_that_did_not_refresh_since_the_last_read_has_no_change():
    rows = mkrows(n=8)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[-1]) + timedelta(seconds=10), flat_bars(30))
    s = v2["strikes"]
    assert s["change_unavailable"] == "no_new_book_since_last_read" and "change_basis" not in s
    assert all(r.get("change") is None for r in B.rows_as_records(s))


def test_a_book_too_old_drops_the_board_and_says_so():
    rows = mkrows(n=8, start=T0 - timedelta(minutes=30))     # newest book 16 minutes old
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert v2["strikes"]["unavailable"] == "book_too_old" and v2["strikes"]["age_min"] > SR.MAX_BOOK_AGE_MIN
    assert "frames" not in v2 and "implied_vol_atm" not in v2.get("scale", {})


def test_the_days_first_book_with_the_prior_sessions_volume_is_left_out():
    rows = mkrows(n=8)
    rows[0]["gex_views"]["vol_side_by_strike"] = [[k, c * 50, p * 50] for k, c, p in rows[0]["gex_views"]["vol_side_by_strike"]]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    assert v2["strikes"]["first_book_dropped"]
    assert v2["frames"]["books_in_series"] == 7
    assert v2["strikes"]["change_basis"] == "5_books"       # eight books: the reference is the third, untouched


def test_crossings_are_listed_strikes_between_then_and_now():
    rows = mkrows(n=8, spot=1290.0)
    frame = {"last_read_at": "09:52", "minutes_since": 8, "spot_then": 1160.0, "spot_change_sigma": 1.3,
             "crossed_since_then": [{"level": 1225.0, "was_labelled_then": "nearest_call_wall", "price_went": "up"}]}
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, frame, SR._ts(rows[3]), flat_bars(30))
    slr = v2["context"]["since_last_read"]
    assert slr["crossed_since_then"] == [{"level": 1200.0, "direction": "up"}, {"level": 1250.0, "direction": "up"}]
    assert {1200.0, 1250.0} <= set(recs(v2))


def test_between_frames_carries_the_earlier_vol_only():
    rows = mkrows(n=8)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), flat_bars(30))
    bf = v2["between_frames"]
    assert bf["implied_vol_at_last_read"] == 0.5 and "implied_vol" not in bf


def test_guard_needs_a_price_for_sides_and_renumbers_ranks():
    sc = _scene()
    sc2 = json.loads(json.dumps(sc)); sc2["price"].pop("live_spot"); sc2["price"].pop("spot_when_book_was_measured")
    obj = {"quiet": False, "read": "Most contracts sit at 1300.", "sides": {"above": {"heavy": 1300.0, "leads_on": []}},
           "clusters": [{"strikes": [1300.0], "center": 1300.0, "rank": 3, "change": "stable"}, {"strikes": [1200.0, 1300.0], "center": 1300.0, "rank": 1, "change": "stable"}],
           "resolved": [], "points": [], "absent": []}
    r = B.check_reading_v2(obj, sc2)
    assert r["sides"] == {"unavailable": "no_spot"}
    assert [c["rank"] for c in r["clusters"]] == [1] and "cluster_rank_renumbered:3->1" in " ".join(r["dropped_observations"])


def test_guard_checks_a_touch_clock_and_a_superlative_against_the_record():
    rows = mkrows(n=8, spot=1290.0)
    bars = flat_bars(20) + [bar(20, 1296.0, 1302.0)] + [bar(i, 1285.0, 1295.0) for i in range(21, 29)]
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), bars)
    assert B._prose_slips_v2("1300 was touched at 09:50 and holds the most contracts", v2) == []
    slips = B._prose_slips_v2("1300 was touched at 09:41; 1150 took the most volume today", v2)
    assert "touch_clock_not_that_strikes:1300:09:41" in slips and "most_volume_unsupported:1150" in slips


def test_the_budget_is_a_note_never_a_drop():
    sc = _scene()
    obj = {"quiet": False, "read": " ".join(["word"] * 60) + " 1300 holds the most contracts.",
           "clusters": [], "sides": {}, "resolved": [], "points": [], "absent": []}
    r = B.check_reading_v2(obj, sc)
    assert r["notes"] == ["read_over_budget:65_words"] and "dropped_observations" not in r


def test_compare_scenes_names_what_went_and_what_came():
    rows = mkrows(n=8)
    v2, v1 = B.build_scene_v2(rows[-1], rows, T0, None, None, flat_bars(30))
    c = B.compare_scenes(v1, v2)
    assert any(p.startswith("magnet") for p in c["removed"])
    assert any(p.startswith("strikes.rows") for p in c["added"])


# ---------------------------------------------------------------- the output guard
def _scene():
    rows = mkrows(n=8)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), flat_bars(30))
    return v2


def test_guard_keeps_a_good_cluster_adds_the_codes_facts_and_sets_the_change_word():
    sc = _scene()
    obj = {"quiet": False, "read": "Most contracts sit at 1300 and price is just under it.",
           "clusters": [{"strikes": [1300.0], "center": 1300.0, "rank": 1, "change": "increased"}],
           "resolved": [], "points": [], "absent": []}
    r = B.check_reading_v2(obj, sc)
    c = r["clusters"][0]
    assert c["center"] == 1300.0 and c["side"] == "above" and c["contracts_share_pp_sum"] > 0
    assert c["change"] == "unknown" and c["on_rule_region"] is False   # no rule handed in
    assert c["change"] in B.CHANGE_WORDS
    if c["on_rule_region"]:
        assert c["change_model"] == "increased" or c["change"] == "increased"
    assert r["quiet"] is False and "abstain" not in r


def test_guard_drops_unlisted_non_adjacent_and_bad_rank_clusters_and_foreign_resolved():
    sc = _scene()
    bad1 = {"strikes": [1300.0, 1325.0], "center": 1300.0, "rank": 1, "change": "stable"}
    bad2 = {"strikes": [1200.0, 1300.0], "center": 1300.0, "rank": 2, "change": "stable"}
    good = {"strikes": [1300.0], "center": 1300.0, "rank": 3, "change": "stable"}
    dup = {"strikes": [1250.0], "center": 1250.0, "rank": 3, "change": "stable"}
    obj = {"quiet": False, "read": "Most contracts sit at 1300.",
           "clusters": [bad1, bad2, good, dup], "resolved": [1999.0], "points": [], "absent": []}
    r = B.check_reading_v2(obj, sc)
    assert [c["center"] for c in r["clusters"]] == [1300.0]
    assert r["resolved"] == []
    reasons = " ".join(r["dropped_observations"])
    assert "unlisted" in reasons and "not_adjacent" in reasons and "rank_invalid" in reasons and "resolved_not" in reasons


def test_guard_keeps_the_models_quiet_flag_unless_the_gates_forced_it():
    sc = _scene()
    obj = {"quiet": False, "read": "Most contracts sit at 1300 and price is just under it.",
           "clusters": [{"strikes": [1300.0], "center": 1300.0, "rank": 1, "change": "stable"}],
           "resolved": [], "points": [], "absent": []}
    r = B.check_reading_v2(obj, sc)
    assert r["quiet"] is False
    obj["clusters"] = []; obj["quiet"] = True
    r = B.check_reading_v2(obj, sc)
    assert r["quiet"] is True and r["abstain"] == "chosen"


def test_guard_still_deletes_a_forecast_word_in_the_prose():
    sc = _scene()
    obj = {"quiet": False, "read": "1300 should hold and price will bounce.",
           "clusters": [], "resolved": [], "points": [], "absent": []}
    r = B.check_reading_v2(obj, sc)
    assert not r.get("read") and r.get("abstain") == "forced"


def test_guard_admits_a_point_at_the_gaps_high():
    rows = mkrows(n=8)
    bars = flat_bars(30)
    bars[28] = bar(28, 1288.0, 1310.0)
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, None, SR._ts(rows[3]), bars)
    obj = {"quiet": False, "read": "Price has held between 1285 and 1310 since your last read.",
           "clusters": [], "resolved": [], "points": [{"level": 1310.0, "note": "the gap's high"}], "absent": []}
    r = B.check_reading_v2(obj, v2)
    assert r["points"] and r["points"][0]["level"] == 1310.0


# ---------------------------------------------------------------- the review's fixes (09-05)
def test_side_slip_reads_the_strike_first_shape_only():
    sc = _scene()                                   # live spot 1290: 1300 is above, 1250 below
    assert B._prose_slips_v2("1300 sits below spot and 1250 above it", sc) == [
        "strike_side_contradicts_spot:1300:below", "strike_side_contradicts_spot:1250:above"]
    assert B._prose_slips_v2("1300 is above price, 1250 just below", sc) == []
    # "price is just above 1700" is the doctrine's own sentence shape: not this guard's business
    assert B._prose_slips_v2("price is just above 1250 and holds below 1300", sc) == []


def test_a_crossed_strike_has_no_listed_age():
    rows = mkrows(n=20, start=T0 - timedelta(minutes=38))
    # 1150 is light and only ever on the list through the nearest-two rule; make it far enough away
    # to leave the list by weight, then bring it back as a crossing
    for r in rows:
        r["gex_views"]["oi_side_by_strike"] = [[k, (1 if k == 1150.0 else c), (0 if k == 1150.0 else p)]
                                               for k, c, p in r["gex_views"]["oi_side_by_strike"]]
    frame = {"last_read_at": "09:40", "minutes_since": 20, "spot_then": 1290.0, "spot_change_sigma": 0.0,
             "crossed_since_then": [{"level": 1150.0, "direction": "down"}]}
    v2, _ = B.build_scene_v2(rows[-1], rows, T0, frame, SR._ts(rows[5]), flat_bars(30))
    r = recs(v2)
    assert 1150.0 in r                              # listed because it was crossed
    weight = set(B.select_strikes(B.surfaces(rows[-1]), B._window(B.surfaces(rows[-1]), *B._ruler(rows[-1])[:2]), B._ruler(rows[-1])[0]))
    if 1150.0 not in weight:
        assert r[1150.0].get("on_list_for_min") is None
