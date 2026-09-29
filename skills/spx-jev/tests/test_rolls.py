"""The roll table, offline: basis steps found only in a roll window, the switch bar, contract names and the guard."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from spx_jev import rolls

ET = ZoneInfo("America/New_York")


def _session(day: date, price: float) -> list[dict]:
    """A regular session of flat 5-minute bars at ``price``."""
    t = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
    return [{"ts": (t + timedelta(minutes=5 * k)).isoformat(), "open": price, "high": price, "low": price, "close": price}
            for k in range(78)]


def _weekdays(first: date, last: date) -> list[date]:
    return [first + timedelta(days=k) for k in range((last - first).days + 1) if (first + timedelta(days=k)).weekday() < 5]


def _es_history(roll_day: date, stray_day: date | None = None):
    """/ES and $SPX from 08-17 to 09-25 with a 0.8% basis step on ``roll_day`` and small daily wobble."""
    fut, ref = [], []
    for i, d in enumerate(_weekdays(date(2026, 8, 17), date(2026, 9, 25))):
        spx = 6600.0 + 3 * i
        basis = (0.9 if d >= roll_day else 0.1) + 0.01 * (i % 3) + (0.5 if d == stray_day else 0.0)
        ref += _session(d, spx)
        fut += _session(d, spx * (1 + basis / 100))
    return fut, ref


def _night(day: date, jump_at: datetime, jump: float) -> list[dict]:
    """The night before ``day``: flat 1-minute bars with one jump."""
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=ET).replace(hour=18)
    out, price = [], 6700.0
    for k in range(120):
        t = start + timedelta(minutes=k)
        if t == jump_at:
            price += jump
        out.append({"ts": t.isoformat(), "open": price, "high": price, "low": price, "close": price})
    return out


def test_a_basis_step_in_the_roll_window_is_a_roll_at_its_switch_bar():
    roll_day = date(2026, 9, 14)
    fut, ref = _es_history(roll_day, stray_day=date(2026, 8, 26))
    switch = datetime(2026, 9, 13, 18, 30, tzinfo=ET)
    found = rolls.detect("/ES", fut, ref, {"2026-09-14": _night(roll_day, switch, 55.0)})
    assert [r["day"] for r in found["rolls"]] == ["2026-09-14"]
    roll = found["rolls"][0]
    assert roll["at"] == switch.isoformat() and roll["jump"] == 55.0 and roll["basis_unit"] == "percent"
    assert roll["basis_step"] > 0.7 and roll["step_vs_typical"] >= rolls.STEP_VS_TYPICAL
    assert {r["day"] for r in found["rejected"]} == {"2026-08-26", "2026-08-27"}   # the stray day's step up and back down
    assert all(r["reason"] == "outside the product's roll window" for r in found["rejected"])


def _five(rows):
    return [{"ts": f"2026-07-30T{hm}:00-04:00", "open": o, "close": c} for hm, o, c in rows]


def test_a_bitcoin_switch_the_largest_jump_put_a_bar_late_moves_to_the_first_bar_at_its_siblings_new_level():
    """/MBT 07-31, five-minute bars only: its largest jump is 17:15 (+215), but /BTC was on the new contract from 17:05
    and /MBT's 17:10 bar already closed at /BTC's new level, 15 points from it; its 17:05 bar, one contract at 64865,
    was still at the old. The sibling that switched first keeps its switch."""
    mbt = _five([("16:55", 64785, 64765), ("17:05", 64865, 64865), ("17:10", 65055, 65025), ("17:15", 65240, 65250),
                 ("17:20", 65160, 64995)])
    btc = _five([("16:55", 64790, 64755), ("17:00", 64750, 64760), ("17:05", 65080, 65080), ("17:10", 65040, 65040),
                 ("17:25", 65040, 65040)])
    day = "2026-07-31"
    found = {"/MBT": {"rolls": [{"symbol": "/MBT", "day": day, "at": "2026-07-30T17:15:00-04:00", "jump": 215.0}]},
             "/BTC": {"rolls": [{"symbol": "/BTC", "day": day, "at": "2026-07-30T17:05:00-04:00", "jump": 320.0}]},
             "/ES": {"rolls": []}}
    out = rolls.align_siblings(found, {"/MBT": {day: mbt}, "/BTC": {day: btc}})
    assert (out["/MBT"]["rolls"][0]["at"], out["/MBT"]["rolls"][0]["jump"]) == ("2026-07-30T17:10:00-04:00", 190.0)
    assert out["/BTC"]["rolls"] == found["/BTC"]["rolls"] and out["/ES"] == found["/ES"]
    assert found["/MBT"]["rolls"][0]["at"] == "2026-07-30T17:15:00-04:00"                # detect's own answer is left as it was
    late = {**found, "/BTC": {"rolls": [{**found["/BTC"]["rolls"][0], "at": "2026-07-30T17:25:00-04:00"}]}}
    assert rolls.align_siblings(late, {"/MBT": {day: mbt}, "/BTC": {day: btc}})["/MBT"]["rolls"][0]["at"] == "2026-07-30T17:15:00-04:00"


def test_a_window_the_data_covers_without_a_step_is_listed():
    fut, ref = _es_history(date(2026, 12, 1))            # no step inside September's window
    found = rolls.detect("/ES", fut, ref, {})
    assert found["rolls"] == [] and found["windows_without_roll"] == [["2026-09-11", "2026-09-17"]]


def test_the_es_expiry_friday_is_no_roll():
    """The expiring contract's settlement steps the basis on the third Friday itself (09-18 by 5.1 times the typical
    step): a step that day is rejected, so it cannot move the table onto a contract Schwab never quotes."""
    fut, ref = _es_history(date(2026, 12, 1), stray_day=date(2026, 9, 18))
    found = rolls.detect("/ES", fut, ref, {})
    assert found["rolls"] == [] and [r["day"] for r in found["rejected"]] == ["2026-09-18", "2026-09-21"]
    assert not rolls.in_roll_window("/ES", date(2026, 12, 18)) and rolls.in_roll_window("/ES", date(2026, 12, 14))


def test_the_bond_basis_is_fitted_against_the_yield():
    fut, ref = [], []
    for i, d in enumerate(_weekdays(date(2026, 7, 20), date(2026, 8, 31))):
        tnx = 42.0 + 0.3 * (i % 5)
        for k, bar in enumerate(_session(d, tnx)):
            y = tnx + 0.01 * k
            ref.append({**bar, "close": y})
            fut.append({**bar, "close": 130.0 - 0.65 * y - (0.3 if d >= date(2026, 8, 24) else 0.0) + 0.002 * (i % 3)})
    found = rolls.detect("/ZN", fut, ref, {})
    assert [r["day"] for r in found["rolls"]] == ["2026-08-24"] and found["unit"] == "points"
    assert abs(found["rolls"][0]["basis_step"] + 0.3) < 0.01


def test_contracts_are_named_back_from_the_quoted_one_in_the_products_cycle():
    assert rolls.shift_contract("/ESZ26", "/ES", -1) == "/ESU26" and rolls.shift_contract("/ESH27", "/ES", -1) == "/ESZ26"
    assert rolls.shift_contract("/BTCV26", "/BTC", -1) == "/BTCU26" and rolls.shift_contract("/MBTZ26", "/MBT", 1) == "/MBTF27"
    named = rolls.name([{"day": "2026-06-15"}, {"day": "2026-09-14"}], "/ES", "/ESZ26")
    assert [(r["from"], r["to"]) for r in named] == [("/ESM26", "/ESU26"), ("/ESU26", "/ESZ26")]


def test_the_roll_windows_follow_each_products_calendar():
    assert rolls.roll_window("/ES", 2026, 12) == (date(2026, 12, 11), date(2026, 12, 17))       # the 12-18 expiry left out
    assert rolls.roll_window("/ES", 2026, 10) is None
    assert rolls.roll_window("/ZN", 2026, 11) == (date(2026, 11, 15), date(2026, 11, 30))
    assert rolls.roll_window("/MBT", 2026, 10) == (date(2026, 10, 23), date(2026, 10, 30))


def _table(tmp_path):
    found = {"rolls": [{"symbol": "/ES", "day": "2026-09-14", "at": "2026-09-13T18:00:00-04:00"}], "rejected": [],
             "windows_without_roll": [], "typical_step": 0.01, "unit": "percent"}
    return rolls.merge(rolls.load(tmp_path), "/ES", found, "2026-08-17", "/ESZ26")    # no table on disk yet


def test_the_guard_refuses_a_comparison_across_the_roll(tmp_path):
    """The 09-14 case: Friday's close was /ESU26, Monday's pre-open /ESZ26, so the stitched 'up' is no move."""
    table = _table(tmp_path)
    friday, sunday_open, monday = (datetime(2026, 9, 11, 16, 0, tzinfo=ET), datetime(2026, 9, 13, 18, 0, tzinfo=ET),
                                   datetime(2026, 9, 14, 9, 27, tzinfo=ET))
    assert not rolls.same_contract(table, "/ES", friday, monday) and not rolls.same_contract(table, "/ES", monday, friday)
    assert rolls.same_contract(table, "/ES", sunday_open, monday)
    assert rolls.same_contract(table, "/ES", friday - timedelta(days=3), friday)
    assert rolls.same_contract(table, "/ZN", friday, monday)                      # another root's roll is not this one's


def test_contract_at_reads_the_table_and_falls_back_to_the_quote(tmp_path):
    table = _table(tmp_path)
    assert rolls.contract_at(table, "/ES", datetime(2026, 9, 11, 16, 0, tzinfo=ET)) == ("/ESU26", "roll_table")
    assert rolls.contract_at(table, "/ES", datetime(2026, 9, 13, 18, 0, tzinfo=ET)) == ("/ESZ26", "roll_table")
    assert rolls.contract_at(table, "/ZN", datetime(2026, 9, 11, 16, 0, tzinfo=ET), "/ZNZ26") == ("/ZNZ26", "quote")
    assert not rolls.pending(table, "/ES", "/ESZ26") and rolls.pending(table, "/ES", "/ESH27")


def test_a_later_detection_advances_the_current_contract_and_keeps_older_rolls(tmp_path):
    table = _table(tmp_path)
    later = {"rolls": [{"symbol": "/ES", "day": "2026-12-14", "at": "2026-12-13T18:00:00-05:00"}], "rejected": [],
             "windows_without_roll": [], "typical_step": 0.01, "unit": "percent"}
    table = rolls.merge(table, "/ES", later, "2026-11-01", "/ESH27")
    assert table["current"]["/ES"] == "/ESH27"
    assert [(r["day"], r["from"], r["to"]) for r in table["rolls"]] == [("2026-09-14", "/ESU26", "/ESZ26"), ("2026-12-14", "/ESZ26", "/ESH27")]
    again = rolls.merge(table, "/ES", later, "2026-11-01", "/ESH27")          # the same span re-detected: nothing moves
    assert again["current"] == table["current"] and again["rolls"] == table["rolls"]
    rolls.save(tmp_path, again)
    assert rolls.load(tmp_path)["rolls"] == again["rolls"]


def test_a_roll_the_re_scan_no_longer_sees_is_kept_and_the_contract_names_hold(tmp_path):
    """The daily job re-detects its last 45 days, whose first day has no day before it to step from: once the
    span starts on the roll day (10-27 for the 09-14 roll) the roll is not found again, and it stays."""
    table = _table(tmp_path)
    none = {"rolls": [], "rejected": [], "windows_without_roll": [], "typical_step": 0.01, "unit": "percent"}
    for since in ("2026-09-14", "2026-09-15", "2026-10-20"):
        table = rolls.merge(table, "/ES", none, since, "/ESZ26")
        assert [(r["day"], r["from"], r["to"]) for r in table["rolls"]] == [("2026-09-14", "/ESU26", "/ESZ26")]
        assert table["current"]["/ES"] == "/ESZ26"
    assert not rolls.same_contract(table, "/ES", datetime(2026, 9, 11, 16, 0, tzinfo=ET), datetime(2026, 9, 14, 9, 28, tzinfo=ET))


def test_a_roll_that_drops_under_the_line_and_is_found_again_moves_the_contract_once(tmp_path):
    """The current contract is counted from the rolls, never stepped per run: a roll found, missed for a day
    (its step under STEP_VS_TYPICAL) and found again is one roll."""
    table = _table(tmp_path)
    dec = {"rolls": [{"symbol": "/ES", "day": "2026-12-14", "at": "2026-12-13T18:00:00-05:00"}], "rejected": [],
           "windows_without_roll": [], "typical_step": 0.01, "unit": "percent"}
    none = {**dec, "rolls": []}
    for found in (dec, none, dec, dec):
        table = rolls.merge(table, "/ES", found, "2026-11-01", "/ESH27")
        assert table["current"]["/ES"] == "/ESH27" and [r["day"] for r in table["rolls"]] == ["2026-09-14", "2026-12-14"]
    assert not rolls.pending(table, "/ES", "/ESH27")
