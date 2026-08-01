"""Quote-derived IV rebuild + the deep-ITM garbage-IV clamp + the front-book
τ convention (M1)."""
import sndk_feed
import synth


def test_rebuild_recovers_true_iv_from_quotes():
    b = synth.book()
    meta = sndk_feed.rebuild_iv(b, synth.SPOT, None)
    assert meta["iv_rebuilt"] > 0
    near = [c for c in b if abs(c["strike"] - synth.SPOT) <= 25
            and c.get("iv_src") == "bs_mid"]
    assert near, "near-the-money strikes must solve from quotes"
    for c in near:
        # true IV 0.5; the ±3% quote spread bounds the solve error
        assert 0.40 <= c["iv"] <= 0.60, (c["strike"], c["right"], c["iv"])


def test_rebuilt_iv_shared_across_rights():
    b = synth.book()
    sndk_feed.rebuild_iv(b, synth.SPOT, None)
    by_ks = {}
    for c in b:
        if c.get("iv_src") == "bs_mid":
            by_ks.setdefault((c["expiry"], c["strike"]), []).append(c["iv"])
    assert by_ks
    for ivs in by_ks.values():
        assert len(set(ivs)) == 1           # parity: one strike, one IV


def test_garbage_itm_call_iv_never_survives():
    # the live pathology verbatim: ITM call 4.9 (stale-spot artifact) with a
    # sane put twin — after the rebuild NO strike near the money carries it
    b = synth.book()
    sndk_feed.rebuild_iv(b, synth.SPOT, None)
    for c in b:
        if abs(c["strike"] - synth.SPOT) <= 0.08 * synth.SPOT and c.get("iv"):
            assert c["iv"] <= sndk_feed._IV_MAX
            assert c["iv"] < 3.0, (c["strike"], c["right"], c["iv"])


def test_parity_clamp_on_provider_fallback():
    # no usable quotes anywhere → the provider-IV fallback path must still
    # clamp the garbage side to its plausible parity twin. Two flavors:
    # out-of-band garbage (5.07 → twin) and in-band-but-implausible-vs-twin
    # (2.4 vs 0.6, > _PARITY_RATIO× → twin).
    b = [synth.leg(1200.0, "call", 4, "2026-07-31", iv=5.07),
         synth.leg(1200.0, "put", 4, "2026-07-31", iv=0.73),
         synth.leg(1210.0, "call", 4, "2026-07-31", iv=2.4),
         synth.leg(1210.0, "put", 4, "2026-07-31", iv=0.6)]
    for c in b:
        c["bid"] = c["ask"] = c["mark"] = None
    meta = sndk_feed.rebuild_iv(b, synth.SPOT, None)
    c1200 = next(c for c in b if c["right"] == "call" and c["strike"] == 1200.0)
    p1200 = next(c for c in b if c["right"] == "put" and c["strike"] == 1200.0)
    c1210 = next(c for c in b if c["right"] == "call" and c["strike"] == 1210.0)
    assert c1200["iv"] == 0.73              # out-of-band → took the twin
    assert c1200["iv_src"] == "parity_twin"
    assert p1200["iv"] == 0.73              # sane side untouched
    assert c1210["iv"] == 0.6               # in-band garbage → ratio clamp
    assert c1210["iv_src"] == "parity_clamp"
    assert meta["iv_clamped"] == 2 and meta["iv_kept_provider"] == 2


def test_no_honest_iv_drops_from_reprice():
    b = [synth.leg(1200.0, "call", 4, "2026-07-31", iv=5.07),
         synth.leg(1200.0, "put", 4, "2026-07-31", iv=0.0)]
    for c in b:
        c["bid"] = c["ask"] = c["mark"] = None
    meta = sndk_feed.rebuild_iv(b, synth.SPOT, None)
    assert all(c["iv"] is None for c in b)  # garbage + unsolvable twin → out
    assert meta["iv_dropped"] == 2


def test_gamma_filled_from_rebuilt_iv():
    b = synth.prepared_book()
    with_iv = [c for c in b if c.get("iv")]
    assert with_iv
    for c in with_iv:
        assert c.get("gamma") and c["gamma"] > 0
        assert c.get("gamma_src") == "bs_iv"


def test_iv_solver_round_trip():
    tau = sndk_feed._tau_for(4, None)
    for right, k in (("call", 1300.0), ("put", 1200.0)):
        px = sndk_feed._bs_price(synth.SPOT, k, 0.45, tau, right)
        iv = sndk_feed._iv_from_price(px, synth.SPOT, k, tau, right)
        assert abs(iv - 0.45) < 1e-3


# --- M1: the front-book clock counts today's remaining session ---------------

def test_thursday_open_iv_not_inflated():
    # Thu 09:30, front weekly dte=1 → ~2 sessions of life. Quotes priced on
    # the true 2-session clock must solve back to ~TRUE_IV; the engine's τ
    # (1 session) read them ×√2 ≈ ×1.41 hot at that open.
    mtc = 390.0
    b = synth.book(front_dte=1, front_expiry="2026-07-31",
                   second_dte=8, second_expiry="2026-08-07", mtc=mtc)
    sndk_feed.rebuild_iv(b, synth.SPOT, mtc)
    near = [c for c in b if c["dte"] == 1 and abs(c["strike"] - synth.SPOT) <= 25
            and c.get("iv_src") == "bs_mid"]
    assert near, "front-book NTM strikes must solve from quotes"
    for c in near:
        # true IV 0.5; the ±3% quote spread bounds the solve error — a ×1.41
        # τ-convention bug lands ~0.71 and fails hard here
        assert 0.40 <= c["iv"] <= 0.60, (c["strike"], c["right"], c["iv"])


def test_tau_front_matches_engine_on_expiry_day_and_after_close():
    for mtc in (390.0, 120.0, None):
        assert sndk_feed._tau_front(0, mtc) == sndk_feed._tau_for(0, mtc)
    # dte>0 outside RTH: today's remainder is 0 → the engine's value, verbatim
    for dte in (1, 4, 11):
        assert sndk_feed._tau_front(dte, None) == sndk_feed._tau_for(dte, None)
    # dte>0 intraday: strictly MORE time than the engine's close-anchored count
    assert sndk_feed._tau_front(1, 390.0) > sndk_feed._tau_for(1, None)
    assert sndk_feed._tau_front(None, 390.0) is None


def test_gamma_invariant_to_tau_convention():
    # BS gamma depends only on σ√τ, so the same market quotes solved+filled on
    # either clock must land the same gamma surface (the M1 invariance check)
    b1 = synth.book(front_dte=1, front_expiry="2026-07-31",
                    second_dte=8, second_expiry="2026-08-07", mtc=390.0)
    b2 = [dict(c) for c in b1]              # identical quotes
    sndk_feed.rebuild_iv(b1, synth.SPOT, 390.0)
    sndk_feed._fill_gamma(b1, synth.SPOT, 390.0)
    sndk_feed.rebuild_iv(b2, synth.SPOT, None)     # after-close = engine clock
    sndk_feed._fill_gamma(b2, synth.SPOT, None)
    checked = 0
    for c1, c2 in zip(b1, b2):
        if c1.get("gamma_src") == "bs_iv" and c2.get("gamma_src") == "bs_iv":
            assert abs(c1["gamma"] - c2["gamma"]) <= 0.01 * abs(c1["gamma"]), \
                (c1["strike"], c1["right"], c1["gamma"], c2["gamma"])
            checked += 1
    assert checked > 20


# --- m6: a nulled IV never leaves stale provider greeks behind ---------------

def test_nulled_iv_nulls_stale_gamma_and_delta():
    b = [synth.leg(1200.0, "call", 4, "2026-07-31", iv=5.07),
         synth.leg(1200.0, "put", 4, "2026-07-31", iv=0.0)]
    for c in b:
        c["bid"] = c["ask"] = c["mark"] = None
        c["gamma"], c["delta"] = 0.0123, 0.55    # provider's stale-spot greeks
    meta = sndk_feed.rebuild_iv(b, synth.SPOT, None)
    assert meta["iv_dropped"] == 2
    for c in b:
        assert c["iv"] is None
        assert c["gamma"] is None and c["delta"] is None


# --- m7: the ITM side clamps at the tighter parity fence ---------------------

def test_itm_side_soft_parity_clamp():
    # the audit case: ITM call 0.90 vs OTM put 0.35 (both in band, ratio 2.6 —
    # under the old 3× fence both survived). The OTM right is extrinsic-only
    # truth, so the ITM side takes the twin at >1.5×.
    b = [synth.leg(1200.0, "call", 4, "2026-07-31", iv=0.90),
         synth.leg(1200.0, "put", 4, "2026-07-31", iv=0.35)]
    for c in b:
        c["bid"] = c["ask"] = c["mark"] = None
    sndk_feed.rebuild_iv(b, synth.SPOT, None)
    call = next(c for c in b if c["right"] == "call")
    put = next(c for c in b if c["right"] == "put")
    assert call["iv"] == 0.35 and call["iv_src"] == "parity_clamp"
    assert put["iv"] == 0.35                # the OTM side is untouched


def test_otm_side_keeps_the_hard_fence():
    # OTM put 1.2 vs ITM call 0.6: ratio 2 — the OTM side is the trusted one,
    # so it only clamps at the hard 3× fence (kept here)
    b = [synth.leg(1200.0, "call", 4, "2026-07-31", iv=0.6),
         synth.leg(1200.0, "put", 4, "2026-07-31", iv=1.2)]
    for c in b:
        c["bid"] = c["ask"] = c["mark"] = None
    sndk_feed.rebuild_iv(b, synth.SPOT, None)
    put = next(c for c in b if c["right"] == "put")
    assert put["iv"] == 1.2 and put.get("iv_src") is None
