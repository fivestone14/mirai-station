"""regions-1 — the rule-found regions block, pinned.

Every time is a book's own clock; every region strike is a listed strike;
nothing blended; absence stated; the same rows give the same block; a window
sliding with price yields stable when nobody traded.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sndk_board as B
import sndk_regions as R
import sndk_read as SR

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 31, 10, 0, tzinfo=ET)

GRID = [1150.0, 1200.0, 1250.0, 1300.0, 1350.0, 1400.0]


def row_at(ts, spot=1290.0, oi=None, vol=None, net=None):
    oi = oi or {k: (100, 50) for k in GRID}
    vol = vol or {k: (10, 10) for k in GRID}
    # gamma follows the contracts unless a test says otherwise, so a uniform
    # gamma never makes every strike a member by the gamma bar
    net = net or {k: float(c + p) for k, (c, p) in oi.items()}
    return {
        "ts": ts.isoformat(), "ticker": "SNDK", "spot": spot, "sigma": 100.0, "atm_iv": 0.5,
        "prior_close": 1250.0, "gamma_sign": "positive", "regime": "neutral",
        "meta": {"chain_spot": spot, "book_asof": ts.isoformat(), "book_source": "pull",
                 "spot_source": "schwab_quote", "expiries": [{"date": "2026-07-31", "dte": 0}]},
        "gex_views": {"magnet": 1300.0, "front_dte": 0,
                      "net_by_strike": [[k, v] for k, v in sorted(net.items())],
                      "oi_side_by_strike": [[k, c, p] for k, (c, p) in sorted(oi.items())],
                      "vol_side_by_strike": [[k, c, p] for k, (c, p) in sorted(vol.items())]},
    }


def day(n, oi_fn=None, vol_fn=None, net_fn=None, spot_fn=None, start=T0 - timedelta(minutes=60)):
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=4 * i)
        rows.append(row_at(ts, spot=(spot_fn(i) if spot_fn else 1290.0),
                           oi=(oi_fn(i) if oi_fn else None), vol=(vol_fn(i) if vol_fn else None),
                           net=(net_fn(i) if net_fn else None)))
    return rows


def block(rows, ref_index=None):
    now = SR._ts(rows[-1]) + timedelta(seconds=30)
    ref = rows[ref_index] if ref_index is not None else None
    _, listed, _, _ = B.strikes_block(rows[-1], rows, now, [], SR._ts(ref) if ref else None)
    return R.regions_block(rows, now, ref, listed, B)


# one heavy strike at 1300 (60% of contracts), everything else light
def heavy(i):
    return {k: ((600, 300) if k == 1300.0 else (20, 10)) for k in GRID}


def test_times_come_off_the_books_and_strikes_are_listed():
    rows = day(8, oi_fn=heavy)
    b = block(rows, ref_index=3)
    times = {SR._book_asof(r).astimezone(ET).strftime("%H:%M") for r in rows}
    assert b["first_book"] in times
    for g in b["regions"]:
        assert g["first_seen"] in times
    _, listed, _, _ = B.strikes_block(rows[-1], rows, SR._ts(rows[-1]) + timedelta(seconds=30), [], SR._ts(rows[3]))
    for g in b["regions"]:
        assert set(g["strikes"]) <= set(listed)
        assert g["center"] in g["strikes"]


def test_no_number_but_strikes_and_counts_and_no_verdict_key():
    rows = day(8, oi_fn=heavy)
    b = block(rows, ref_index=3)
    allowed = {"strikes", "center", "by", "first_seen", "present", "change"}
    for g in b["regions"]:
        assert set(g) <= allowed, set(g)
        for k in g:
            assert not any(w in k for w in ("score", "strength", "weight", "rank", "share"))
    assert set(b) <= {"rule", "books", "first_book", "regions", "resolved", "absent", "reference_unavailable", "none_at_threshold"}


def test_members_are_adjacent_on_the_grid():
    def two(i):
        return {k: ((600, 300) if k in (1300.0, 1350.0) else (20, 10)) for k in GRID}
    rows = day(8, oi_fn=two)
    b = block(rows, ref_index=3)
    regs = [g for g in b["regions"] if 1300.0 in g["strikes"]]
    assert regs and regs[0]["strikes"] == [1300.0, 1350.0]
    def split(i):
        return {k: ((600, 300) if k in (1250.0, 1350.0) else (20, 10)) for k in GRID}
    rows = day(8, oi_fn=split)
    b = block(rows, ref_index=3)
    assert all(len(g["strikes"]) == 1 for g in b["regions"])


def test_hysteresis_enter_at_eight_stay_to_six():
    # one far anchor at 1150 holds the bulk so the strikes beside 1300 are
    # never members themselves; 1300 is the strike under test
    def path(pct):
        d = {k: (1, 0) for k in GRID}
        d[1300.0] = (pct * 10, 0)
        d[1150.0] = (1000 - pct * 10 - 4, 0)
        return d
    novol = lambda i: {k: (0, 0) for k in GRID}
    # at exactly 7% throughout: never enters
    rows = day(8, oi_fn=lambda i: path(7), vol_fn=novol)
    b = block(rows, ref_index=3)
    assert all(1300.0 not in g["strikes"] for g in b["regions"])
    # enters at 10%, then sinks to 7%: stays, marked held
    rows = day(8, oi_fn=lambda i: path(10) if i < 4 else path(7), vol_fn=novol)
    b = block(rows, ref_index=3)
    g = next(g for g in b["regions"] if 1300.0 in g["strikes"])
    assert g["strikes"] == [1300.0] and g["by"] == "held"
    # sinks to 5%: leaves
    rows = day(8, oi_fn=lambda i: path(10) if i < 4 else path(5), vol_fn=novol)
    b = block(rows, ref_index=3)
    assert all(1300.0 not in g["strikes"] for g in b["regions"])


def test_new_and_resolved_use_the_reference_book():
    # 1300 heavy only from book 5 on: new. 1200 heavy only up to book 3: resolved.
    def oi(i):
        # 1400 stays heavy all day so the light strikes hold about 3% and
        # never qualify on their own
        d = {k: (20, 10) for k in GRID}
        d[1400.0] = (600, 300)
        if i >= 5:
            d[1300.0] = (600, 300)
        if i <= 3:
            d[1200.0] = (600, 300)
        return d
    rows = day(9, oi_fn=oi)
    b = block(rows, ref_index=3)
    g = next(g for g in b["regions"] if 1300.0 in g["strikes"])
    assert g["change"] == "new"
    assert all(1200.0 not in g["strikes"] for g in b["regions"])
    res = b["resolved"]
    assert res and res[0]["center"] == 1200.0
    assert res[0]["last_seen"] == SR._book_asof(rows[3]).astimezone(ET).strftime("%H:%M")


def test_no_reference_gives_resolved_null_and_no_change_word():
    rows = day(3, oi_fn=heavy)
    b = block(rows, ref_index=None)
    assert b["resolved"] is None
    assert b["reference_unavailable"] == "no_earlier_book"
    assert all("change" not in g for g in b["regions"])


def test_nothing_at_threshold_is_stated():
    rows = day(8)   # every strike equal: no strike reaches 8% of six? 1/6 = 16.7% each: all qualify
    # make them all light against a heavy strike OUTSIDE the listed set: impossible by rule, so use uneven small shares
    def flat(i):
        return {k: (100, 0) for k in GRID}
    rows = day(8, oi_fn=flat)
    b = block(rows, ref_index=3)
    # six equal strikes each hold 16.7%, all qualify; so test the empty case with gamma and contracts both spread over many strikes
    grid = [1100.0 + 10 * j for j in range(30)]
    def many(i):
        return {k: (100, 0) for k in grid}
    rows = []
    for i in range(8):
        ts = T0 - timedelta(minutes=60) + timedelta(minutes=4 * i)
        r = row_at(ts, spot=1250.0, oi=many(i), vol={k: (1, 1) for k in grid}, net={k: 1.0 for k in grid})
        rows.append(r)
    b = block(rows, ref_index=3)
    assert b["regions"] == [] and b["none_at_threshold"] is True


def test_same_rows_same_block_and_first_seen_does_not_move():
    rows = day(10, oi_fn=heavy)
    b1 = block(rows, ref_index=4)
    b2 = block(rows, ref_index=4)
    assert b1 == b2
    b3 = block(rows[:-1], ref_index=4)
    f_now = next(g for g in b1["regions"] if 1300.0 in g["strikes"])["first_seen"]
    f_prev = next(g for g in b3["regions"] if 1300.0 in g["strikes"])["first_seen"]
    assert f_now == f_prev


def test_window_sliding_with_price_reads_stable_when_nobody_traded():
    # price walks 40 points across the series; open interest and volume never change
    rows = day(12, oi_fn=heavy, spot_fn=lambda i: 1270.0 + 4 * i)
    b = block(rows, ref_index=4)
    g = next(g for g in b["regions"] if 1300.0 in g["strikes"])
    assert g["change"] == "stable"


def test_gamma_rising_as_spot_approaches_with_flat_volume_is_stable():
    rows = day(12, oi_fn=heavy, net_fn=lambda i: {k: (1.0 + i if k == 1300.0 else 1.0) for k in GRID})
    b = block(rows, ref_index=4)
    g = next(g for g in b["regions"] if 1300.0 in g["strikes"])
    assert g["change"] == "stable"


def test_volume_arriving_reads_increased():
    def vol(i):
        return {k: ((10 + 120 * i, 10) if k == 1300.0 else (10, 10)) for k in GRID}
    rows = day(12, oi_fn=heavy, vol_fn=vol)
    b = block(rows, ref_index=4)
    g = next(g for g in b["regions"] if 1300.0 in g["strikes"])
    assert g["change"] == "increased"


def test_block_is_small():
    import json
    rows = day(12, oi_fn=heavy)
    b = block(rows, ref_index=4)
    assert len(json.dumps(b)) < 1000
