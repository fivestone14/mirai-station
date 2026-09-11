"""Review item #8 (2026-09-11) — a book still carrying yesterday's volume.

The vendor's first print of the day usually still holds the prior session's
cumulative volume: 26 of 28 recorded open mornings, and the table's volume
leader was wrong on 19 of 29 first reads. The old guard compared the first book
with the second, so it could never fire on the one read that needed it. These
pin the detector (a count equal to the prior session's last count is that
session's count), the clock fallback that only ever WITHHOLDS, and every edge
the owner asked to be checked: a noon restart, a missing or dark prior session,
a record that stops short of the close, a pre-open book, a Monday after an
expiry, a repeated cached book, the next read after a withheld one, a genuinely
zero-volume open, and the checker reading a board with no volume on it."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sndk_board as B
import sndk_read as SR
from test_board import mkrow, flat_bars, recs

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 9, 15, tzinfo=ET)          # a Tuesday
PRIOR = datetime(2026, 9, 14, tzinfo=ET)        # the Monday before it

YEST = {1150.0: (300, 150), 1200.0: (2400, 1200), 1250.0: (1800, 900), 1300.0: (5400, 2700),
        1350.0: (1200, 600), 1400.0: (600, 300)}


def at(d, h, m):
    return d.replace(hour=h, minute=m)


def fronted(r, front="2026-09-18"):
    r["meta"]["expiries"] = [{"date": front, "dte": 3}, {"date": "2026-09-25", "dte": 10}]
    return r


def write_prior(tmp_path, last_hhmm=(15, 58), vol=None, front="2026-09-18", day=PRIOR, next_vol=None):
    """The prior session's last diary row, with its volume counts."""
    d = tmp_path / "sndk_reversion"
    d.mkdir(parents=True, exist_ok=True)
    r = fronted(mkrow(at(day, *last_hhmm), vol=vol), front)
    if next_vol is not None:
        r["meta"]["expiries"] = [{"date": front, "dte": 0}, {"date": "2026-09-18", "dte": 7}]
        r["gex_views"]["next_dte"] = 7
        r["gex_views"]["vol_side_by_strike_next"] = [[k, c, p] for k, (c, p) in sorted(next_vol.items())]
    (d / f"{day.strftime('%Y-%m-%d')}.jsonl").write_text(json.dumps(r) + "\n")


def build(rows, now, last_read_ts=None, **kw):
    rows = [fronted(r) for r in rows]
    return B.build_scene_v2(rows[-1], rows, now, None, last_read_ts, flat_bars(5), **kw)


def withheld(v2):
    return [a for a in v2["strikes"].get("absent", []) if a.startswith("volume:")]


def test_a_first_book_equal_to_yesterdays_close_loses_its_volume(tmp_path):
    write_prior(tmp_path, vol=YEST)
    v2, _ = build([mkrow(at(DAY, 9, 31), vol=YEST)], at(DAY, 9, 33))
    s = v2["strikes"]
    assert not {"vol_calls", "vol_puts", "rank_by_volume_today", "vol_added_per_book"} & set(s["columns"])
    assert "still carries the prior session's counts" in withheld(v2)[0]
    assert all("vol_calls" not in r for r in s["rows"])
    # contracts are open interest plus volume, so the carried volume must leave
    # the shares too: 1300 holds 810 of 1,755 open-interest contracts (46.15%),
    # where yesterday's volume folded in would have made it 64.08%
    assert recs(v2)[1300.0]["contracts_share_pp"] == 46.15


def test_a_noon_restart_keeps_fresh_volume(tmp_path):
    """The reader's first read of the day at noon is not a first book: its counts
    have grown past yesterday's, so nothing is withheld."""
    write_prior(tmp_path, vol=YEST)
    grown = {k: (c + 7, p + 9) for k, (c, p) in YEST.items()}
    v2, _ = build([mkrow(at(DAY, 12, 0), vol=grown)], at(DAY, 12, 2))
    assert "rank_by_volume_today" in v2["strikes"]["columns"] and not withheld(v2)


def test_with_no_prior_record_the_clock_only_withholds_and_only_early(tmp_path):
    v2, _ = build([mkrow(at(DAY, 9, 31), vol=YEST)], at(DAY, 9, 33))
    assert "vol_calls" not in v2["strikes"]["columns"]
    assert "cannot be told apart" in withheld(v2)[0]
    later, _ = build([mkrow(at(DAY, 9, 50), vol=YEST)], at(DAY, 9, 52))
    assert "vol_calls" in later["strikes"]["columns"]
    noon, _ = build([mkrow(at(DAY, 12, 0), vol=YEST)], at(DAY, 12, 2))
    assert "vol_calls" in noon["strikes"]["columns"]


def test_a_prior_record_that_stops_before_the_close_is_not_trusted(tmp_path):
    write_prior(tmp_path, last_hhmm=(12, 12), vol={k: (c // 2, p // 2) for k, (c, p) in YEST.items()})
    v2, _ = build([mkrow(at(DAY, 9, 31), vol=YEST)], at(DAY, 9, 33))
    assert "cannot be told apart" in withheld(v2)[0]


def test_a_dark_session_between_makes_the_prior_record_the_wrong_day(tmp_path):
    write_prior(tmp_path, vol=YEST, day=datetime(2026, 9, 10, tzinfo=ET))   # Thu; Fri and Mon unrecorded
    v2, _ = build([mkrow(at(DAY, 9, 31), vol=YEST)], at(DAY, 9, 33))
    assert "cannot be told apart" in withheld(v2)[0]


def test_a_book_measured_before_the_open_is_withheld(tmp_path):
    write_prior(tmp_path, vol={k: (1, 1) for k in YEST})
    v2, _ = build([mkrow(at(DAY, 9, 20), vol=YEST)], at(DAY, 9, 22))
    assert "before the open" in withheld(v2)[0]


def test_monday_after_an_expiry_compares_with_fridays_next_book(tmp_path):
    """Friday's front week expired, so Monday's front book was Friday's NEXT
    book, and that is the array its counts are compared with."""
    fri, mon = datetime(2026, 9, 11, tzinfo=ET), datetime(2026, 9, 14, tzinfo=ET)
    write_prior(tmp_path, vol={k: (5, 3) for k in YEST}, front="2026-09-11", day=fri, next_vol=YEST)
    r = mkrow(at(mon, 9, 31), vol=YEST)
    r["meta"]["expiries"] = [{"date": "2026-09-18", "dte": 4}]
    v2, _ = B.build_scene_v2(r, [r], at(mon, 9, 33), None, None, flat_bars(5))
    assert "still carries" in withheld(v2)[0]


def test_a_repeated_scan_of_a_withheld_book_stays_withheld(tmp_path):
    write_prior(tmp_path, vol=YEST)
    a = mkrow(at(DAY, 9, 31), vol=YEST)
    b = mkrow(at(DAY, 9, 33), vol=YEST, book_asof=at(DAY, 9, 31))   # the disk cache re-served
    v2, _ = build([a, b], at(DAY, 9, 34))
    assert "vol_calls" not in v2["strikes"]["columns"]


def test_the_next_read_never_compares_against_a_withheld_book(tmp_path):
    write_prior(tmp_path, vol=YEST)
    rows = [mkrow(at(DAY, 9, 31), vol=YEST)] + [
        mkrow(at(DAY, 9, 31 + 4 * i), vol={k: (5 * i, 3 * i) for k in YEST}) for i in range(1, 4)]
    v2, _ = build(rows, at(DAY, 9, 44), last_read_ts=at(DAY, 9, 33))
    s = v2["strikes"]
    assert s["change_unavailable"] == "earlier_book_carried_prior_session_volume"
    assert all(r.get("change") is None for r in B.rows_as_records(s))
    assert "resolved" not in (v2.get("regions") or {})
    assert v2["frames"]["book_times"][0] == "09:35" and s["first_book_dropped"]


def test_joined_and_left_are_not_claimed_across_the_switch_back_to_volume(tmp_path):
    """The first read's list was picked on open interest alone; the next is
    picked with today's volume. Diffing the two would report the switch as
    strikes joining and leaving, so across it nothing is claimed — and a list
    drawn on the same basis still diffs normally."""
    write_prior(tmp_path, vol=YEST)
    first = [mkrow(at(DAY, 9, 31), vol=YEST)]
    v_first, _ = build(first, at(DAY, 9, 33))
    shown = B.listed_strikes(v_first["strikes"]) + [1450.0]
    rows = first + [mkrow(at(DAY, 9, 31 + 4 * i), vol={k: (5 * i, 3 * i) for k in YEST}) for i in range(1, 4)]
    across, _ = build(rows, at(DAY, 9, 44), last_read_ts=at(DAY, 9, 33),
                      strikes_sent_before=shown, sent_before_without_volume=True)
    assert "left_since_reference" not in across["strikes"]
    same, _ = build(rows, at(DAY, 9, 44), last_read_ts=at(DAY, 9, 33),
                    strikes_sent_before=shown, sent_before_without_volume=False)
    assert same["strikes"]["left_since_reference"] == [1450.0]


def test_a_genuinely_zero_volume_open_is_not_carried_and_ranks_no_one(tmp_path):
    """Nothing traded yet is not yesterday's count. But a strike with no trades
    has no volume rank: tied at zero, the lowest strike used to rank first."""
    write_prior(tmp_path, vol={k: (0, 0) for k in YEST})
    v2, _ = build([mkrow(at(DAY, 9, 31), vol={k: (0, 0) for k in YEST})], at(DAY, 9, 33))
    assert not withheld(v2)
    assert all(r.get("rank_by_volume_today") is None for r in B.rows_as_records(v2["strikes"]))


def _reply(read, sides=None):
    return {"quiet": False, "read": read,
            "sides": sides or {"above": {"heavy": None, "leads_on": []},
                               "below": {"heavy": None, "leads_on": []}},
            "clusters": [], "resolved": [], "points": [], "absent": []}


def test_the_checker_reads_a_board_with_no_volume_on_it(tmp_path):
    """"Every measure" means every measure the board shows, and "the most
    volume" cannot stand on a board that shows none."""
    write_prior(tmp_path, vol=YEST)
    v2, v1 = build([mkrow(at(DAY, 9, 31), vol=YEST)], at(DAY, 9, 33))
    rs = recs(v2)
    leader = next(k for k, r in rs.items() if r.get("rank_by_contracts") == 1
                  and r.get("rank_by_dealer_gamma") == 1)
    ok = B.check_reading_v2(_reply(f"{leader:g} leads on every measure the board shows this morning."),
                            v2, v1.get("regions_rule"))
    assert ok.get("read"), ok.get("dropped_observations")
    vol = B.check_reading_v2(_reply("1400 took the most volume today."), v2, v1.get("regions_rule"))
    assert not vol.get("read")
    assert any("most_volume_unsupported" in d for d in vol.get("dropped_observations") or [])
