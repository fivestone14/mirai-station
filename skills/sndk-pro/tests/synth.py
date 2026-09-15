"""Synthetic SNDK data for the sndk-pro suite, and the one home of every helper
more than one test file builds from.

The books: a $5 grid on a ~$1250 stock, weekly expiries, quotes priced from
Black-Scholes at a known IV so the quote-derived IV rebuild has a recoverable
truth. The diary rows: the reader's, the board's, read_once's and the scene's,
each carrying what its payload reads. A test file imports these from here and
never from another test file, so renaming a test file cannot break a second
one (conftest refuses to run a suite where one does)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sndk_board as B
import sndk_feed
import sndk_read as SR

SPOT = 1250.0
TRUE_IV = 0.5

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 7, 31, 10, 0, tzinfo=ET)
OPEN_AT = datetime(2026, 7, 31, 9, 30, tzinfo=ET)
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=ET)   # a Friday, mid-session

# conftest swaps both model calls for a guard before every test. These are the
# real ones, taken when conftest first imports this module and so before any
# guard is in place, for the tests that fake the subprocess under them to check
# the command line the station actually runs.
_REAL_CALL_THE_MODEL = SR.call_the_model
_REAL_CALL_THE_MODEL_V2 = B.call_the_model_v2


def leg(strike: float, right: str, dte: int, expiry: str, *,
        iv=None, oi: int = 200, vol: int = 100, spot: float = SPOT,
        quote_iv: float = TRUE_IV, mtc=None) -> dict:
    """One contract with live-looking quotes (BS mid at quote_iv vs `spot`)
    and an optionally-garbage provider `iv` (None = provider sent null).
    `mtc` prices the quote on the intraday front-book clock (M1 tests);
    None = after the close, where the engine and front-book clocks agree."""
    tau = sndk_feed._tau_front(dte, mtc)
    px = sndk_feed._bs_price(spot, strike, quote_iv, tau, right)
    bid = round(px * 0.97, 2)
    ask = round(px * 1.03, 2)
    if bid <= 0:
        bid = ask = None            # untradeably far wing: no usable quote
    return {"right": right, "strike": float(strike), "dte": dte,
            "expiry": expiry, "root": "SNDK", "iv": iv,
            "open_interest": oi, "volume": vol, "gamma": None,
            "delta": None, "theta": None, "vega": None,
            "bid": bid, "ask": ask, "mark": None,
            "bid_size": 1, "ask_size": 1}


def book(front_dte: int = 4, front_expiry: str = "2026-07-31",
         second_dte: int = 11, second_expiry: str = "2026-08-07",
         spot: float = SPOT, lo: float = 1150.0, hi: float = 1350.0,
         mtc=None) -> list:
    """Two weekly expiries, both rights, $5 grid. Provider IVs mimic the live
    pathology: garbage on the ITM side, sane-ish on the OTM side."""
    out = []
    for exp, dte in ((front_expiry, front_dte), (second_expiry, second_dte)):
        k = lo
        while k <= hi:
            call_iv = 4.9 if k < spot else 0.52      # ITM calls: stale-spot garbage
            put_iv = 0.0 if k >= spot else 0.55      # ITM puts: unsolvable → 0.0
            out.append(leg(k, "call", dte, exp, iv=call_iv, spot=spot, mtc=mtc))
            out.append(leg(k, "put", dte, exp, iv=put_iv, spot=spot, mtc=mtc))
            k += 5.0
    return out


def prepared_book(**kw) -> list:
    """A book after the feed's post-processing (IV rebuilt + gamma filled) —
    what sndk_chain hands the views layer."""
    b = book(**kw)
    spot = kw.get("spot", SPOT)
    mtc = kw.get("mtc")
    sndk_feed.rebuild_iv(b, spot, mtc)
    sndk_feed._fill_gamma(b, spot, mtc)
    return b


# --- the reader's diary row (test_read's mkrow) -------------------------------
def reader_row(mass, up=2.0, dn=0.2, spot=1200.0, sigma=100.0, ts=None, **kw):
    """A diary row carrying only what the reader touches."""
    row = {
        "ts": (ts or T0).isoformat(), "ticker": "SNDK", "spot": spot,
        "sigma": sigma, "regime": kw.pop("regime", "trending"),
        "gamma_sign": kw.pop("gamma_sign", "negative"),
        "call_wall": kw.pop("call_wall", 1400.0),
        "put_wall": kw.pop("put_wall", 1000.0),
        "prior_close": kw.pop("prior_close", 1250.0),
        "gex_views": {"mass_by_strike": mass, "magnet": kw.pop("magnet", None),
                      "shove": {"shove_up_margin": up, "shove_down_margin": dn}},
        "profile_ladder": kw.pop("profile_ladder", {}),
    }
    row.update(kw)
    return row


def _last_call(minutes_ago=47, **gate_kw):
    r = reader_row([[1300, 60], [1100, 20]], **gate_kw)
    return {"ts": (T0 - timedelta(minutes=minutes_ago)).isoformat(),
            "wall_s": 9.9, "gate": SR.state_for_next_wake(r)}


# --- the board's diary row (test_board's mkrow) and its minute bars -----------
def board_row(ts, spot=1290.0, chain_spot=None, sigma=100.0, oi=None, vol=None, net=None,
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
    return [board_row(start + timedelta(minutes=i * step), **kw) for i in range(n)]


def bar(i, lo, hi, close=None, vol=1000.0):
    ts = OPEN_AT + timedelta(minutes=i)
    return {"ts": ts.isoformat(), "open": lo, "high": hi, "low": lo,
            "close": hi if close is None else close, "volume": vol}


def flat_bars(n, lo=1285.0, hi=1295.0):
    return [bar(i, lo, hi) for i in range(n)]


def recs(v2):
    return {r["strike"]: r for r in B.rows_as_records(v2["strikes"])}


# --- a book still carrying yesterday's volume (test_carried_volume) -----------
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
    r = fronted(board_row(at(day, *last_hhmm), vol=vol), front)
    if next_vol is not None:
        r["meta"]["expiries"] = [{"date": front, "dte": 0}, {"date": "2026-09-18", "dte": 7}]
        r["gex_views"]["next_dte"] = 7
        r["gex_views"]["vol_side_by_strike_next"] = [[k, c, p] for k, (c, p) in sorted(next_vol.items())]
    (d / f"{day.strftime('%Y-%m-%d')}.jsonl").write_text(json.dumps(r) + "\n")


# --- read_once's diary rows and model reply (test_read_once) ------------------
def _diary_row(ts, spot=1200.0):
    return {"ts": ts.isoformat(), "ticker": "SNDK", "spot": spot,
            "sigma": 100.0, "regime": "trending", "gamma_sign": "negative",
            "call_wall": 1400.0, "put_wall": 1000.0, "prior_close": 1250.0,
            "gex_views": {"mass_by_strike": [[1300, 60], [1100, 20]],
                          "shove": {"shove_up_margin": 2.0,
                                    "shove_down_margin": 0.2}},
            "profile_ladder": {},
            "meta": {"book_asof": ts.isoformat()}}


def _diary_row_with_board(ts, spot=1200.0):
    """A diary row carrying the per-strike surfaces the Strikes Payload reads."""
    row = _diary_row(ts, spot)
    oi ={1100.0: (20, 10), 1150.0: (30, 15), 1200.0: (40, 20), 1250.0: (60, 30), 1300.0: (600, 300)}
    row["gex_views"].update({
        "magnet": 1300.0,
        "oi_side_by_strike": [[k, c, p] for k, (c, p) in oi.items()],
        "vol_side_by_strike": [[k, c // 10, p // 10] for k, (c, p) in oi.items()],
        "net_by_strike": [[k, float(c + p)] for k, (c, p) in oi.items()],
        "mass_by_strike": [[k, c + p + c // 10 + p // 10] for k, (c, p) in oi.items()],
    })
    row["meta"]["chain_spot"] = spot
    return row


def _v2_reply(**over):
    obj = {"quiet": False,
           "read": "Price has held between 1200 and 1208 since your last read. 1300 holds the most contracts above and 1150 leads below.",
           "sides": {"above": {"heavy": 1300.0, "leads_on": ["contracts"]}, "below": {"heavy": 1150.0, "leads_on": []}},
           "clusters": [{"strikes": [1300.0], "center": 1300.0, "rank": 1, "change": "stable"}],
           "resolved": [], "points": [{"level": 1300.0, "note": "most contracts"}], "absent": []}
    obj.update(over)
    return obj


# --- the scene's diary row and the scenes built from it, read at NOW (test_scene_v2)
def rich_row(ts=None, spot=1200.0, sigma=100.0, **kw):
    """A ROW_V 3 diary row with every payload-v2 source populated."""
    row = {
        "ts": (ts or NOW).isoformat(), "ticker": "SNDK", "spot": spot,
        "sigma": sigma, "sigma_live": sigma, "atm_iv": kw.pop("atm_iv", 1.5875),
        "regime": "trending", "gamma_sign": "negative",
        "gamma_flip": kw.pop("gamma_flip", 1206.0),
        "call_wall": 1300.0, "put_wall": 1100.0,
        "prior_close": 1250.0,
        "vwap": kw.pop("vwap", 1212.0),
        "iv_skew": kw.pop("iv_skew", {"down_share": 0.55, "skew_pts": 8.0,
                                      "put_side_iv": 1.7, "call_side_iv": 1.5,
                                      "n_down": 5, "n_up": 5, "skew_v": 1}),
        "flows_front": kw.pop("flows_front",
                              {"cex": -4_276_191.0, "vex": 255_992.0,
                               "charm_wall": 1370.0, "vanna_wall": 1300.0,
                               "basis": "open_interest", "n": 200,
                               "clock": "front_book", "flows_v": 1}),
        "range_ruler": kw.pop("range_ruler",
                              {"em_points": 40.0, "quality": "ok"}),
        "profile_ladder": kw.pop("profile_ladder",
                                 {"ct": 1231.0, "hvl": 1206.0, "pt": 1181.0,
                                  "state": "negative transition"}),
        "dex_views": kw.pop("dex_views", {"net_dex_total": 3.93e9}),
        "gex_views": {
            "magnet": 1300.0, "front_dte": 3,
            "mass_by_strike": kw.pop("mass", [[1300, 60.0], [1100, 30.0],
                                              [1200, 10.0]]),
            "vol_gross_by_strike": kw.pop("vol_gross", [[1300, 5000],
                                                        [1100, 2000]]),
            # a realistic $5 grid: the meaningful walls ride on a dense field
            # of sub-floor strikes so cluster_walls infers the true step
            "net_by_strike": kw.pop("nbs", (
                [[1100, -6.0], [1150, -7.0], [1240, 8.0], [1245, 7.0],
                 [1300, 6.5]]
                + [[k, 0.1] for k in range(1105, 1300, 5)
                   if k not in (1150, 1240, 1245)])),
            "shove": {"shove_up_margin": 2.0, "shove_down_margin": 0.2},
        },
    }
    row.update(kw)
    return row


def scene_of(row, rows=None):
    rows = rows or [row]
    return SR.build_scene(row, SR.magnet_band(row), [], rows, NOW)


def _iv_rows(iv_then, iv_now):
    rows = [rich_row(ts=NOW - timedelta(minutes=35), atm_iv=iv_then),
            rich_row(ts=NOW - timedelta(minutes=15), atm_iv=(iv_then + iv_now) / 2),
            rich_row(ts=NOW, atm_iv=iv_now)]
    return rows


def _book_rows(**kw):
    """Six staggered scans — the momentum window's own minimum, so every one of
    the eight book-derived blocks is on the board and the gate has all eight to
    take away."""
    return [rich_row(ts=NOW - timedelta(minutes=(5 - i) * 2), **kw)
            for i in range(6)]


def _oi_surface(strikes):
    """An oi_by_strike surface keyed BY STRIKE, so a window that walks with
    spot carries the same OI on the strikes it still contains."""
    return [[float(k), 1000 + int(k) % 97] for k in strikes]


def _oi_rows(*surfaces):
    rows = []
    for i, s in enumerate(surfaces):
        r = rich_row(ts=NOW - timedelta(minutes=(len(surfaces) - 1 - i) * 2))
        if s is not None:
            r["gex_views"]["oi_by_strike"] = s
        rows.append(r)
    return rows


# a surface with call clusters only — the put side is measured EMPTY
_CALLS_ONLY = ([[1240, 8.0], [1245, 7.0], [1300, 6.5]]
               + [[k, 0.1] for k in range(1105, 1300, 5) if k not in (1240, 1245)])


def _scene_with_bars(tmp_path):
    import sndk_bars as SB
    rows = [rich_row(ts=NOW - timedelta(minutes=(40 - i) * 2), spot=1200.0 + (i % 4))
            for i in range(40)]
    t0 = NOW - timedelta(minutes=80)
    # strikes-3: minute 60 wicks $27 over the box (minutes are $8, the bar $16),
    # so this is also the scene that carries a box break and the frame's bar
    bars = [{"ts": (t0 + timedelta(minutes=i)).isoformat(), "open": 1200.0,
             "high": 1230.0 if i == 60 else 1206.0 if i == 50 else 1203.0,
             "low": 1195.0, "close": 1201.0, "volume": 10.0} for i in range(80)]
    SB.write_day(NOW.date().isoformat(), bars, NOW)
    lc = {"ts": (NOW - timedelta(minutes=12)).isoformat(), "spot": 1180.0,
          "gate": {"spot": 1180.0}}
    return SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), [], rows, NOW,
                          since_last_read=SR.frame_since_last_read(
                              rows[-1], rows, lc, "price ran", False, NOW, bars=bars))


def _every_scene_shape(tmp_path):
    """Scenes covering every conditional leaf the builder can write.

    One scene cannot reach them all — a censored wall age, a board with no flip,
    an absent chain_spot, a repeated book and a history flag are mutually
    exclusive states of the same tape — so the guards that walk them union them."""
    prev = NOW - timedelta(days=1)
    diary = tmp_path / "sndk_reversion"
    diary.mkdir(parents=True, exist_ok=True)
    (diary / f"{prev.date().isoformat()}.jsonl").write_text("\n".join(
        json.dumps(rich_row(ts=prev - timedelta(minutes=i * 2))) for i in range(40)) + "\n")

    def scene(rows, frozen=None):
        return SR.build_scene(rows[-1], SR.magnet_band(rows[-1]), frozen or [],
                              rows, NOW)

    moved = rich_row(); moved["_ran_30m_sigma"] = 0.31
    repeat_asof = (NOW - timedelta(minutes=3)).isoformat()
    dense = [[k, 0.1] for k in range(1105, 1300, 5)
             if k not in (1150, 1240, 1245, 1260, 1265)]
    walked = [rich_row(ts=NOW - timedelta(minutes=(5 - i) * 2),
                       nbs=([[1100, -6.0], [1150, -7.0], [1240, 8.0],
                             [1245, 7.0], [1300, 6.5]] if i else
                            [[1100, -6.0], [1150, -7.0], [1260, 9.0],
                             [1265, 7.0]]) + dense)
              for i in range(6)]
    ran = [rich_row(ts=NOW - timedelta(minutes=(40 - i) * 2), spot=1200.0 + i * 6)
           for i in range(40)]
    ran[-1]["_ran_30m_sigma"] = 0.9
    same_oi = _oi_surface(range(1100, 1165, 5))

    return [
        scene([rich_row()]),
        scene([rich_row(meta={})]),                        # the fallback ruler tag
        scene([rich_row(meta={"chain_spot": 1200.0,
                              "spot_source": "schwab_quote"})]),   # the SILENT feed
        # sr-9: `spot_feed` ships only when the feed is NOT the usual one, so
        # the guard needs a scene where it is something else or the doctrine's
        # sentence about it would look like a sentence outliving its field.
        scene([rich_row(meta={"chain_spot": 1200.0,
                              "spot_source": "backup_poll"})]),    # spot_feed fires
        scene([rich_row(meta={"chain_spot": 1190.0})]),     # both spots + the gap
        scene([moved]),                                     # moved_last_30min_sigma
        scene([rich_row(profile_ladder=None, gamma_flip=None)]),   # no flip at all
        scene([rich_row()], [{"field": "magnet", "value": 1.0, "for_min": 99}]),
        scene([rich_row(ts=NOW - timedelta(minutes=35),
                        dex_views={"net_dex_total": 3.0e9}),
               rich_row(ts=NOW, dex_views={"net_dex_total": 3.93e9})]),
        scene(_iv_rows(1.50, 2.30)),                        # regime.vol_trend
        scene(_book_rows()),                                # censored wall ages
        # sr-9: freshness_rules ships only when it dropped something, so the
        # only scene carrying `blocks_dropped_this_scan` is one with a dead book.
        SR.build_scene(_book_rows()[-1], SR.magnet_band(_book_rows()[-1]), [],
                       _book_rows(), NOW + timedelta(minutes=7)),
        scene(walked),                                      # an exact wall age
        scene([rich_row(ts=NOW - timedelta(minutes=2),
                        meta={"chain_spot": 1200.0, "book_asof": repeat_asof}),
               rich_row(ts=NOW,
                        meta={"chain_spot": 1200.0, "book_asof": repeat_asof})]),
        scene(_oi_rows(same_oi, same_oi)),
        scene(ran),                                         # the history flags
        # obs-3: a scene carrying the since-last-read frame — a crossing, so the
        # frozen level, the delta, the clock string and the range all ship
        SR.build_scene(rich_row(spot=1520.0), SR.magnet_band(rich_row()), [],
                       [rich_row(ts=NOW - timedelta(minutes=30), spot=1490.0),
                        rich_row(spot=1520.0)], NOW,
                       since_last_read=SR.frame_since_last_read(
                           rich_row(spot=1520.0),
                           [rich_row(ts=NOW - timedelta(minutes=30), spot=1490.0),
                            rich_row(spot=1520.0)],
                           # obs-4: the gate freezes the LADDER's wall, never the
                           # diary scalar — rich_row's ladder has no call rung above
                           # 1490, so the frozen level is stated outright here
                           {"ts": (NOW - timedelta(minutes=30)).isoformat(),
                            "gate": {"spot": 1490.0, "call_wall": 1500.0,
                                     "magnet": 1300.0}},
                           "call wall crossed", False, NOW)),
        # obs-4: a frame whose put side was empty at BOTH readings — the block
        # says so in one word (walls_absent_then_and_now), never with a number
        SR.build_scene(rich_row(nbs=_CALLS_ONLY), SR.magnet_band(rich_row()), [],
                       [rich_row(ts=NOW - timedelta(minutes=30), nbs=_CALLS_ONLY),
                        rich_row(nbs=_CALLS_ONLY)], NOW,
                       since_last_read=SR.frame_since_last_read(
                           rich_row(nbs=_CALLS_ONLY),
                           [rich_row(ts=NOW - timedelta(minutes=30), nbs=_CALLS_ONLY),
                            rich_row(nbs=_CALLS_ONLY)],
                           {"ts": (NOW - timedelta(minutes=30)).isoformat(),
                            "gate": SR.state_for_next_wake(rich_row(nbs=_CALLS_ONLY))},
                           "heartbeat", False, NOW)),
        # obs-5: a scene with the minute-bar sidecar present — extremes and
        # boxes from wicks, data_sources.minute_bars, the labels saying so
        _scene_with_bars(tmp_path),
        # a scene carrying Python-found candidates: the gamma sign differs
        # between two distinct books, which is `changed_this_scan`
        scene([rich_row(ts=NOW - timedelta(minutes=4), gamma_sign="negative",
                        meta={"chain_spot": 1200.0,
                              "book_asof": (NOW - timedelta(minutes=4)).isoformat()}),
               rich_row(ts=NOW, gamma_sign="positive",
                        meta={"chain_spot": 1200.0,
                              "book_asof": NOW.isoformat()})]),
    ]
