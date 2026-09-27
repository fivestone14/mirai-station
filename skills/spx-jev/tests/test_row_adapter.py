"""The row adapter: an SPX diary row cut down to exactly the fields the labeller reads."""
from __future__ import annotations

from conftest import at, make_row
from spx_jev.row_adapter import DATED_BAND_FIELDS, LABELLER_FIELDS, LABELLER_VIEWS, labeller_row


def test_the_labeller_row_keeps_the_named_fields_and_drops_the_rest():
    raw = make_row(at(12, 0), 7700.0)
    row = labeller_row(raw)
    assert set(row) <= set(LABELLER_FIELDS) | set(LABELLER_VIEWS) | {"dated_gex"}
    assert "siege" not in row and "watchtower" not in row and "gamma_flip" not in row
    assert row["spot"] == 7700.0 and row["sigma"] == 75.0 and row["range_ruler"]["em_consumed"] == 0.8
    assert "magnet" not in row["gex_views"] and row["gex_views"]["pin_top_share"] == 0.06
    assert row["dex_views"] == {"dex_above_spot": 0.43} and row["profile_ladder"] == {"state": "positive"}
    assert row["dated_gex"]["staleness"] == "fresh" and all(set(b) == set(DATED_BAND_FIELDS) for b in row["dated_gex"]["bands"])


def test_a_row_that_is_not_a_usable_spx_row_is_none():
    assert labeller_row(make_row(at(12, 0), 7700.0, ticker="SNDK")) is None
    assert labeller_row({**make_row(at(12, 0), 7700.0), "spot": None}) is None
    assert labeller_row({"spot": 7700.0}) is None
    assert labeller_row("not a row") is None


def test_a_missing_or_null_field_is_absent_never_none():
    row = labeller_row(make_row(at(12, 0), 7700.0, call_wall=None, gex_views={"magnet": 1.0}))
    assert "call_wall" not in row and "gex_views" not in row
    assert all(v is not None for v in row.values())
