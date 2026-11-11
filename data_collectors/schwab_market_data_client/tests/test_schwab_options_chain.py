"""schwab_options_chain — shaping Schwab's chain JSON into the station's contract rows. No network."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schwab_options_chain as soc  # noqa: E402

TODAY = date(2026, 11, 11)


def contract(strike, root="SPXW", **over):
    row = {"symbol": f"{root:<6}261111C{int(strike * 1000):08d}", "optionRoot": root,
           "strikePrice": strike, "bid": 11.7, "ask": 11.8, "mark": 11.75, "bidSize": 37,
           "askSize": 4, "totalVolume": 37492, "openInterest": 3076, "volatility": 13.718,
           "delta": 0.526, "gamma": 0.016, "theta": -9.059, "vega": 0.745}
    row.update(over)
    return row


def payload(calls, puts, spot=7600.0, **over):
    def by_strike(rows):
        out = {}
        for expiry_key, row in rows:
            out.setdefault(expiry_key, {}).setdefault(str(row["strikePrice"]), []).append(row)
        return out
    body = {"symbol": "$SPX", "status": "SUCCESS", "underlyingPrice": spot, "isDelayed": False,
            "callExpDateMap": by_strike(calls), "putExpDateMap": by_strike(puts)}
    body.update(over)
    return body


def test_a_contract_comes_out_in_the_station_shape():
    book = soc.normalize(payload([("2026-11-11:0", contract(7605.0))], []), TODAY)
    (c,) = book["contracts"]
    assert c["right"] == "call" and c["strike"] == 7605.0 and c["dte"] == 0
    assert c["expiry"] == "2026-11-11" and c["root"] == "SPXW"
    assert abs(c["iv"] - 0.13718) < 1e-9                       # percent -> decimal
    assert c["gamma"] is None                                  # Schwab's rounded gamma is not used
    assert (c["open_interest"], c["volume"], c["bid_size"], c["ask_size"]) == (3076, 37492, 37, 4)
    assert (c["bid"], c["ask"], c["mark"], c["delta"]) == (11.7, 11.8, 11.75, 0.526)
    assert book["spot"] == 7600.0 and book["expiries"] == ["2026-11-11"] and book["delayed"] is False


def test_schwab_blanks_become_none():
    row = contract(7605.0, volatility=-999.0, delta=-999.0, theta="NaN", vega=None, bid=0.0)
    (c,) = soc.normalize(payload([("2026-11-11:0", row)], []), TODAY)["contracts"]
    assert c["iv"] is None and c["delta"] is None and c["theta"] is None and c["vega"] is None
    assert c["bid"] == 0.0                                     # a real zero bid stays a zero


def test_roots_strikes_and_days_are_cut_to_the_window():
    calls = [("2026-11-11:0", contract(7600.0)),
             ("2026-11-11:0", contract(7600.0, root="SPX")),      # monthly root
             ("2026-11-11:0", contract(9000.0)),                  # outside the strike window
             ("2026-11-20:9", contract(7600.0)),                  # beyond max_dte
             ("2026-11-10:-1", contract(7600.0))]                 # already expired
    book = soc.normalize(payload(calls, []), TODAY, roots={"SPXW"}, strike_lo=7000.0,
                         strike_hi=8200.0, max_dte=7)
    assert [(c["root"], c["strike"], c["dte"]) for c in book["contracts"]] == [("SPXW", 7600.0, 0)]
    both = soc.normalize(payload(calls, []), TODAY, roots={"SPXW", "SPX"}, strike_lo=7000.0,
                         strike_hi=8200.0, max_dte=7)
    assert sorted(c["root"] for c in both["contracts"]) == ["SPX", "SPXW"]


def test_the_side_ledger_counts_each_root_expiry_and_side():
    calls = [("2026-11-11:0", contract(7600.0)), ("2026-11-11:0", contract(7605.0))]
    puts = [("2026-11-11:0", contract(7595.0))]
    sides = soc.normalize(payload(calls, puts), TODAY)["sides"]
    assert sides == [
        {"root": "SPXW", "exp": "2026-11-11", "side": "call", "rows": 2, "raw_rows": 2, "dte": 0,
         "k_lo": 7600.0, "k_hi": 7605.0},
        {"root": "SPXW", "exp": "2026-11-11", "side": "put", "rows": 1, "raw_rows": 1, "dte": 0,
         "k_lo": 7595.0, "k_hi": 7595.0}]


def test_no_spot_or_a_failed_answer_is_no_book():
    assert soc.normalize(payload([], [], spot=None), TODAY) is None
    assert soc.normalize(payload([], [], status="FAILED"), TODAY) is None
    assert soc.normalize(None, TODAY) is None
    fallback = payload([("2026-11-11:0", contract(7600.0))], [], spot=None, underlying={"last": 7601.0})
    assert soc.normalize(fallback, TODAY)["spot"] == 7601.0


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeClient:
    """Refuses the whole-window ask (Schwab's 502 on a huge answer), answers single days."""

    def __init__(self, whole_window_status=200):
        self.whole_window_status = whole_window_status
        self.asked = []

    def get_option_chain(self, symbol, **params):
        d0, d1 = params["from_date"], params["to_date"]
        self.asked.append((d0, d1))
        if d0 != d1 and self.whole_window_status != 200:
            return FakeResponse(self.whole_window_status)
        key = f"{d0.isoformat()}:{(d0 - TODAY).days}"
        return FakeResponse(200, payload([(key, contract(7600.0))], [(key, contract(7600.0))]))


def test_fetch_book_takes_the_window_in_one_call():
    client = FakeClient()
    book = soc.fetch_book(client, "$SPX", TODAY, days=7, roots={"SPXW"}, lo_frac=0.92, hi_frac=1.08)
    assert len(client.asked) == 1 and book["chunks"] == 1 and book["provider"] == "schwab"
    assert len(book["contracts"]) == 2


def test_fetch_book_falls_back_to_one_weekday_at_a_time():
    client = FakeClient(whole_window_status=502)
    book = soc.fetch_book(client, "$SPX", TODAY, days=7, roots={"SPXW"})
    weekdays = [d0 for d0, d1 in client.asked[1:]]
    assert all(d.weekday() < 5 for d in weekdays) and len(weekdays) == 6     # Wed..Wed, no weekend
    assert book["chunks"] == 6 and len(book["contracts"]) == 12
    assert book["expiries"] == sorted(d.isoformat() for d in weekdays)
