"""The payload's `levels` block (2026-09-10): the two facts the phone's
three-levels card needed that the scene does not carry, the count behind the
most-contracts strike and which gamma pile is heaviest on the board.

The card went on 2026-09-19, since the chart draws the same three levels and
its key explains them. The server still builds the block, so its half stays
here. The card's half went with the card; the tests of what it shared with the
chart's key (the sheet, the tap that opens it, the shell bridge) are in
test_phone_route.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import snapshot

ET = ZoneInfo("America/New_York")


# --- the server's half: a count and a heaviest-pile check -------------------

import sys  # noqa: E402
if str(snapshot._SNDK_PRO_DIR) not in sys.path:
    sys.path.insert(0, str(snapshot._SNDK_PRO_DIR))
import sndk_read as _R  # noqa: E402  — the reader's own module, as the route uses it


def _row(**gv):
    base = {"mass_by_strike": [[1600.0, 9307.0], [1700.0, 16419.0], [1750.0, 8435.0]],
            "vol_gross_by_strike": [[1600.0, 6579], [1700.0, 14066]],
            "net_by_strike": [[1595.0, -0.15], [1600.0, -3.82], [1605.0, -1.9],
                              [1650.0, -2.6], [1700.0, 0.3], [1760.0, 1.7]]}
    base.update(gv)
    return {"spot": 1694.12, "sigma": 64.5438, "gex_views": base}


def _scene(**walls):
    w = {"call": [{"strike": 1760.0, "cluster_share_of_book_gamma_pp": 16.8}],
         "put": [{"strike": 1650.0, "cluster_share_of_book_gamma_pp": 25.7}],
         "put_heaviest_wall_behind_the_ladder": {"strike": 1600.0,
                                                 "cluster_share_of_book_gamma_pp": 57.4}}
    w.update(walls)
    return {"magnet": {"top_strikes": [{"strike": 1700.0, "share_of_book_gamma_pp": 14.63}]},
            "walls": w}


def test_the_count_is_the_one_that_chose_the_strike():
    """mass_by_strike is open interest plus today's volume, calls and puts
    together — the argmax IS the scene's magnet. The count is what chose it, so
    the count is what is shown; the scene's own number for it is a share of
    contracts shipped under a gamma name, and never reaches the card."""
    lv = snapshot._levels_display(_R, _row(), _scene())
    mc = lv["most_contracts"]
    assert mc["strike"] == 1700.0 and mc["contracts"] == 16419
    assert mc["traded_today"] == 14066
    # read off the engine's own constant: 1.5 x the row's sigma
    import lefteye_gex_box as gx
    assert mc["window_dollars"] == round(gx.MAG_WINDOW_SIGMA * 64.5438, 2) == 96.82


def test_a_traded_figure_larger_than_the_count_is_dropped():
    """The two come from the same front-expiry contracts, so traded <= total by
    construction. If they ever disagree the sentence "N contracts, and M of
    them traded today" with M > N is unreadable, so the figure goes rather than
    the sentence lying."""
    lv = snapshot._levels_display(_R, _row(vol_gross_by_strike=[[1700.0, 99999]]), _scene())
    assert "traded_today" not in lv["most_contracts"]


def test_the_heaviest_pile_is_found_on_the_bars_own_measure_and_placed():
    """The heaviest cluster on the whole net_by_strike surface, as a share of
    that surface — the same number the wall bars show — plus which role it plays
    on the card, so the sheet's "why it can look light" sentence can name it."""
    lv = snapshot._levels_display(_R, _row(), _scene())
    h = lv["heaviest"]
    assert h["strike"] == 1600.0 and h["role"] == "put_further"
    assert h["holds_most_contracts"] is False           # 1,700 is not inside that pile
    # the denominator is the WHOLE surface; the numerator only what survived
    # the 25% concentration floor (1,595 at 0.15 did not), as in walls_ladder
    tot = 0.15 + 3.82 + 1.9 + 2.6 + 0.3 + 1.7
    assert h["share_pct"] == round((3.82 + 1.9) / tot * 100, 1)


def test_a_heaviest_pile_that_is_no_wall_has_no_role():
    """A call-heavy pile below price qualifies as neither side and is on no
    part of the card. role None is what hides the sheet's sentence, which would
    otherwise name a level the screen never draws."""
    lv = snapshot._levels_display(_R, _row(), _scene(put=[], put_heaviest_wall_behind_the_ladder={}))
    assert lv["heaviest"]["role"] is None


def test_nothing_measured_is_nothing_shipped():
    assert snapshot._levels_display(_R, {"spot": 1600.0}, {}) is None


def test_a_torn_surface_costs_only_its_own_half():
    """One bad pair in the gamma surface used to throw inside the clustering
    rule and take a perfectly good contract count down with it, so the card
    printed "Count not measured" beside a strike it had measured. The halves
    now fail apart, and the rule is handed the cleaned pairs."""
    row = _row(net_by_strike=[[1600.0, -3.82], [1650.0, None], [1605.0, "x"], [1760.0, 1.7]])
    lv = snapshot._levels_display(_R, row, _scene(call=["not a dict", {"strike": 1760.0}]))
    assert lv["most_contracts"]["contracts"] == 16419          # untouched by the tear
    assert lv["heaviest"]["strike"] == 1600.0                  # the clean pairs still cluster
    # and a surface that is ALL tear leaves the count standing on its own
    lv = snapshot._levels_display(_R, _row(net_by_strike=[[1650.0, None]]), _scene())
    assert "heaviest" not in lv and lv["most_contracts"]["strike"] == 1700.0


def test_the_levels_ride_the_wrapper_and_reach_no_model(tmp_path, monkeypatch):
    """A sibling of the scene on the display side of the fence, like
    `instrument`: not in the Strikes Payload, not in the legacy scene the wake
    gate reads, and not in the user_prompt the model is sent."""
    monkeypatch.setenv("MIRAI_STATE_DIR", str(tmp_path))
    t = datetime(2026, 8, 19, 13, 1, tzinfo=ET)
    row = {"ticker": "SNDK", "ts": t.isoformat(), "spot": 1586.2, "sigma": 80.0,
           "prior_close": 1554.5, "gamma_sign": "negative", "regime": "trending",
           "gex_views": {"front_dte": 2, "magnet": 1600.0,
                         "mass_by_strike": [[1600.0, 40.0], [1700.0, 30.0], [1500.0, 25.0]],
                         "net_by_strike": [[1550.0, -3.0e6], [1600.0, -2.0e6], [1700.0, 4.0e6]]},
           "meta": {"expiries": [{"date": "2026-08-21", "dte": 2}]}}
    d = tmp_path / "sndk_reversion"
    d.mkdir(parents=True)
    (d / "2026-08-19.jsonl").write_text(json.dumps(row) + "\n")
    p = snapshot.sndk_payload(datetime(2026, 8, 19, 13, 2, tzinfo=ET))

    assert p["levels"]["most_contracts"] == {"strike": 1600.0, "contracts": 40,
                                             "window_dollars": 120.0}
    assert p["levels"]["heaviest"]["strike"] == 1550.0
    assert p["levels"]["heaviest"]["role"] == "put"
    assert p["levels"]["heaviest"]["holds_most_contracts"] is True   # 1,600 sits in 1550-1600
    assert "levels" not in p["scene"] and "levels" not in p["legacy"]["scene"]
    assert "most_contracts" not in p["user_prompt"] and "holds_most_contracts" not in p["user_prompt"]

