"""Tests for lefteye_dex — the dealer DELTA exposure surface (SHADOW, record-only).

Pure math, no network. Covers the dealer sign convention on BOTH rights (mirror
of the gamma engine's calls +, puts −), the structural-long invariant the header
doctrine declares, aggregation + the diary window, zero-OI / missing-IV fail-open
(an outage is never a zero-delta book), the flow-signed refinement hook, and the
Watchtower's advisory dealer_delta_dex block."""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lefteye_dex as dx  # noqa: E402
import lefteye_gex_box as gv  # noqa: E402
import watchtower as wt  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 24, 11, 38, tzinfo=ET)
SPOT = 5000.0


def _c(k, right, dte=1, oi=1000, vol=0, iv=0.2, **kw):
    return {"strike": k, "right": right, "dte": dte,
            "open_interest": oi, "volume": vol, "iv": iv, **kw}


def _chain(spot=SPOT):
    """A plain two-tenor book: OI-carrying strikes ±3% of spot, both rights."""
    ch = []
    for k in range(4850, 5151, 50):
        ch.append(_c(k, "call", dte=3))
        ch.append(_c(k, "put", dte=3))
        ch.append(_c(k, "call", dte=0, oi=0, vol=800))
        ch.append(_c(k, "put", dte=0, oi=0, vol=800))
    return ch


# --------------------------------------------------------------------- pricing
class TestBsDelta:
    def test_bounds_and_parity(self):
        # call ∈ (0,1), put ∈ (−1,0), and Δc − Δp = 1 (r=q=0 put-call parity)
        for k in (4800.0, 5000.0, 5200.0):
            dc = dx._bs_delta(SPOT, k, 0.2, 0.01, "call")
            dp = dx._bs_delta(SPOT, k, 0.2, 0.01, "put")
            assert 0.0 < dc < 1.0
            assert -1.0 < dp < 0.0
            assert abs((dc - dp) - 1.0) < 1e-12

    def test_moneyness(self):
        # deep ITM call → Δ ≈ 1; deep OTM call → Δ ≈ 0 (short clock sharpens it)
        assert dx._bs_delta(SPOT, 4700.0, 0.2, 0.002, "call") > 0.95
        assert dx._bs_delta(SPOT, 5300.0, 0.2, 0.002, "call") < 0.05

    def test_degenerate_inputs_are_zero(self):
        assert dx._bs_delta(SPOT, 5000.0, 0.0, 0.01, "call") == 0.0
        assert dx._bs_delta(SPOT, 5000.0, 0.2, 0.0, "put") == 0.0


# ------------------------------------------------------------- dealer sign
class TestDealerSign:
    def test_call_dealer_long_is_positive(self):
        ex = dx._dealer_dex(_c(4900, "call"), SPOT, "open_interest", None)
        assert ex is not None and ex > 0

    def test_put_dealer_short_is_positive_too(self):
        # dealers SHORT the puts: −Δput > 0 — the structural-long artifact,
        # asserted on purpose so a future "fix" of the sign is a loud test break
        ex = dx._dealer_dex(_c(5100, "put"), SPOT, "open_interest", None)
        assert ex is not None and ex > 0

    def test_itm_outweighs_otm_on_equal_weight(self):
        itm = dx._dealer_dex(_c(4850, "call"), SPOT, "open_interest", None)
        otm = dx._dealer_dex(_c(5150, "call"), SPOT, "open_interest", None)
        assert itm > otm

    def test_non_repricing_contract_is_none(self):
        # the shared _reprices predicate: no IV / −999 sentinel / no weight / no dte
        assert dx._dealer_dex(_c(4900, "call", iv=None), SPOT, "open_interest", None) is None
        assert dx._dealer_dex(_c(4900, "call", iv=-9.99), SPOT, "open_interest", None) is None
        assert dx._dealer_dex(_c(4900, "call", oi=0), SPOT, "open_interest", None) is None
        assert dx._dealer_dex(_c(4900, "call", dte=None), SPOT, "open_interest", None) is None


# ------------------------------------------------------------- aggregation
class TestDexViews:
    def test_structural_long_invariant_and_word(self):
        v = dx.dex_views(_chain(), SPOT)
        assert v is not None
        assert v["net_dex_total"] > 0            # long BY CONSTRUCTION (doctrine)
        assert v["dex_word"].startswith("dealers_need_to_SELL_on_rallies")
        assert "BY CONSTRUCTION" in v["dex_word"]  # the caveat rides the word
        assert v["dex_v"] == dx.DEX_V and v["basis"] == "open_interest"

    def test_by_strike_sums_both_rights(self):
        two = [_c(5000, "call"), _c(5000, "put")]
        v = dx.dex_views(two, SPOT)
        assert len(v["net_dex_by_strike"]) == 1
        k, tot = v["net_dex_by_strike"][0]
        assert k == 5000.0
        parts = [dx._dealer_dex(c, SPOT, "open_interest", None) for c in two]
        assert abs(tot - sum(parts)) < 0.01
        assert abs(v["net_dex_total"] - sum(parts)) < 0.01

    def test_window_trims_by_strike_but_not_the_total(self):
        far = _c(4800, "call")                    # inside pick_basis ±5%, outside ±3%
        base = dx.dex_views(_chain(), SPOT)
        v = dx.dex_views(_chain() + [far], SPOT)
        assert v["net_dex_total"] > base["net_dex_total"]
        assert min(k for k, _ in v["net_dex_by_strike"]) >= SPOT * (1 - gv.NBS_WINDOW)

    def test_above_below_shares(self):
        v = dx.dex_views(_chain(), SPOT)
        assert abs(v["dex_above_spot"] + v["dex_below_spot"] - 1.0) < 0.001
        # pile extra put OI overhead → the above-spot share must grow
        heavy = _chain() + [_c(5150, "put", oi=50000)]
        assert dx.dex_views(heavy, SPOT)["dex_above_spot"] > v["dex_above_spot"]

    def test_0dte_twin(self):
        v = dx.dex_views(_chain(), SPOT)
        assert v["net_dex_0dte"] is not None
        assert 0 < v["net_dex_0dte"] < v["net_dex_total"]
        dated_only = [c for c in _chain() if c["dte"] != 0]
        assert dx.dex_views(dated_only, SPOT)["net_dex_0dte"] is None


# --------------------------------------------------------------- fail-open
class TestFailOpen:
    def test_no_book_no_spot(self):
        assert dx.dex_views([], SPOT) is None
        assert dx.dex_views(_chain(), 0.0) is None
        assert dx.dex_views(_chain(), None) is None

    def test_iv_blackout_is_no_read_not_zero(self):
        # strikes + OI but no usable IV: a feed outage must never print a
        # zero-delta book (slide_flip's blackout-guard doctrine)
        hollow = [_c(k, r, iv=None) for k in (4900, 5000, 5100) for r in ("call", "put")]
        assert dx.dex_views(hollow, SPOT) is None
        sentinel = [_c(k, r, iv=-9.99) for k in (4900, 5000, 5100) for r in ("call", "put")]
        assert dx.dex_views(sentinel, SPOT) is None

    def test_weightless_book_is_no_read(self):
        bare = [_c(k, "call", oi=0, vol=0) for k in (4900, 5000, 5100)]
        assert dx.dex_views(bare, SPOT) is None

    def test_one_bad_contract_is_skipped_not_fatal(self):
        good = [_c(4950, "call"), _c(5050, "put")]
        v = dx.dex_views(good + [_c(5000, "call", iv=-9.99)], SPOT)
        assert v["n"] == 2
        assert abs(v["net_dex_total"] - dx.dex_views(good, SPOT)["net_dex_total"]) < 0.01


# ------------------------------------------------------------- flow-signed
class TestFlowSigned:
    def test_customer_call_buying_leaves_dealers_short_delta(self):
        # customers net BUY calls → dealers net SHORT calls → dealer delta < 0
        tape = _c(5000, "call", dte=0, oi=0, vol=0, flow_buy_q=1000, flow_sell_q=0)
        v = dx.dex_views(_chain() + [tape], SPOT)
        assert v["dex_flow_signed"] < 0
        assert v["dex_flow_word"].endswith("BUYS the underlying")
        assert v["dex_flow_n_strikes"] == 1

    def test_customer_put_buying_leaves_dealers_long_delta(self):
        # customers net BUY puts (Δ < 0) → dealers short a negative delta → > 0
        tape = _c(5000, "put", dte=0, oi=0, vol=0, flow_buy_q=1000, flow_sell_q=0)
        v = dx.dex_views(_chain() + [tape], SPOT)
        assert v["dex_flow_signed"] > 0
        assert v["dex_flow_word"].endswith("SELLS the underlying")

    def test_zero_weight_strike_still_counts_for_the_tape(self):
        # the flow leg's weight IS the tape — a strike with no OI/volume on the
        # naive basis must still contribute (own loop, own filter)
        tape = _c(5000, "call", dte=0, oi=0, vol=0, flow_buy_q=400, flow_sell_q=100)
        v = dx.dex_views(_chain() + [tape], SPOT)
        assert v["dex_flow_by_strike"] and v["dex_flow_by_strike"][0][0] == 5000.0

    def test_no_ticks_means_unknown_never_zero(self):
        v = dx.dex_views(_chain(), SPOT)
        assert v["dex_flow_signed"] is None
        assert v["dex_flow_by_strike"] is None
        assert v["dex_flow_word"] is None
        assert v["dex_flow_n_strikes"] is None

    def test_naive_surface_untouched_by_annotations(self):
        plain = dx.dex_views(_chain(), SPOT)
        tape = _c(5000, "call", dte=0, oi=0, vol=0, flow_buy_q=1000, flow_sell_q=0)
        assert dx.dex_views(_chain() + [tape], SPOT)["net_dex_total"] == plain["net_dex_total"]


# ------------------------------------------------- watchtower advisory block
def _row(dex=None):
    t = {"ts": NOW.isoformat(), "ticker": "SPX", "spot": 7450.29, "sigma": 106.9,
         "gamma_sign": "negative", "regime": "pinning",
         "reversion_extreme": {"fired": False, "armed": False},
         "gex_views": {"magnet": 7420.0}}
    if dex is not None:
        t["dex_views"] = dex
    return t


class TestWatchtowerDexBlock:
    def test_payload_carries_advisory_dex_block(self):
        rec = dx.dex_views(_chain(), SPOT)
        p = wt.build_payload(_row(rec))
        blk = p["dealer_delta_dex"]
        assert blk["net_dealer_delta"].startswith("dealers_need_to_SELL_on_rallies")
        assert "bn underlying-equivalent" in blk["magnitude"]
        assert "UNCALIBRATED" in blk["magnitude"]
        assert blk["delta_mass_above_spot_share"] == rec["dex_above_spot"]
        assert "ADVISORY THIS ERA" in blk["note"] and "~44%" in blk["note"]
        # no tape annotation this row → the flow-signed line stays absent
        assert "flow_signed_read" not in blk
        # payload hygiene: no absolute strike levels leak through the block
        import json as _json
        assert "5000" not in _json.dumps(blk)

    def test_flow_signed_line_rides_when_the_tape_spoke(self):
        tape = _c(5000, "put", dte=0, oi=0, vol=0, flow_buy_q=1000, flow_sell_q=0)
        rec = dx.dex_views(_chain() + [tape], SPOT)
        blk = wt.build_payload(_row(rec))["dealer_delta_dex"]
        assert blk["flow_signed_read"].startswith("flow-signed")

    def test_absent_read_is_absent_never_null(self):
        assert "dealer_delta_dex" not in wt.build_payload(_row())
        assert "dealer_delta_dex" not in wt.build_payload(_row({"net_dex_total": None}))
